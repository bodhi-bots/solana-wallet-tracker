"""
Solana Wallet Tracker - Telegram bot
Posts a message in your chat or group whenever a tracked wallet buys or sells a token.

Commands:
  /start                       - help
  /track <address> [label]     - start tracking a wallet (e.g. /track 5Q54...xYz whale)
  /untrack <address or label>  - stop tracking a wallet
  /wallets                     - list tracked wallets in this chat
"""

import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["TELEGRAM_TOKEN"]  # from @BotFather - never put it in code or on GitHub
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
CHECK_INTERVAL = 20  # seconds between checks
DATA_FILE = Path(__file__).with_name("wallets.json")

WSOL_MINT = "So11111111111111111111111111111111111111112"
ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58
MIN_SOL = 0.001  # ignore dust

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # keeps the token out of the logs
log = logging.getLogger(__name__)


# ---------- Storage ----------
# {address: {"last_sig": str | None, "chats": {chat_id: label}}}

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def save_data() -> None:
    DATA_FILE.write_text(json.dumps(wallets, indent=2), encoding="utf-8")


wallets = load_data()
symbol_cache: dict[str, str] = {}


# ---------- Solana RPC ----------

async def rpc(client: httpx.AsyncClient, method: str, params: list):
    r = await client.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


async def token_symbol(client: httpx.AsyncClient, mint: str) -> str:
    """Look up a token's ticker via DexScreener, cached. Falls back to a short mint address."""
    if mint in symbol_cache:
        return symbol_cache[mint]
    symbol = f"{mint[:4]}…{mint[-4:]}"
    try:
        r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
        pairs = r.json().get("pairs") or []
        for p in pairs:
            if p.get("baseToken", {}).get("address") == mint:
                symbol = p["baseToken"]["symbol"]
                break
    except Exception as e:
        log.debug("Symbol lookup failed for %s: %s", mint, e)
    symbol_cache[mint] = symbol
    return symbol


def balance_changes(tx: dict, wallet: str) -> tuple[float, dict[str, float]]:
    """Return (SOL change, {mint: token change}) for one wallet in one transaction."""
    meta = tx["meta"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]

    sol = 0.0
    if wallet in keys:
        i = keys.index(wallet)
        sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9

    tokens: dict[str, float] = {}
    for sign, balances in ((-1, meta.get("preTokenBalances") or []), (1, meta.get("postTokenBalances") or [])):
        for b in balances:
            if b.get("owner") != wallet:
                continue
            amount = float(b["uiTokenAmount"].get("uiAmountString") or 0)
            tokens[b["mint"]] = tokens.get(b["mint"], 0.0) + sign * amount

    # Wrapped SOL counts as SOL
    sol += tokens.pop(WSOL_MINT, 0.0)
    tokens = {m: d for m, d in tokens.items() if abs(d) > 0}
    return sol, tokens


def fmt_amount(x: float) -> str:
    x = abs(x)
    if x >= 1_000_000:
        return f"{x / 1_000_000:,.2f}M"
    if x >= 1_000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.6f}".rstrip("0").rstrip(".")


def short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}"


async def describe(client: httpx.AsyncClient, tx: dict, wallet: str, label: str, sig: str) -> str | None:
    """Turn a transaction into a readable alert, or None if it's not interesting."""
    if tx is None or tx["meta"].get("err") is not None:
        return None  # failed transaction

    sol, tokens = balance_changes(tx, wallet)
    name = html.escape(label or short(wallet))
    links = (
        f'<a href="https://solscan.io/tx/{sig}">Tx</a> · '
        f'<a href="https://solscan.io/account/{wallet}">Wallet</a>'
    )

    lines = []
    for mint, delta in tokens.items():
        sym = html.escape(await token_symbol(client, mint))
        chart = f'<a href="https://dexscreener.com/solana/{mint}">Chart</a>'
        if delta > 0 and sol < 0:
            lines.append(f"🟢 <b>{name}</b> bought {fmt_amount(delta)} <b>{sym}</b> for {fmt_amount(sol)} SOL · {chart}")
        elif delta < 0 and sol > 0:
            lines.append(f"🔴 <b>{name}</b> sold {fmt_amount(delta)} <b>{sym}</b> for {fmt_amount(sol)} SOL · {chart}")
        elif delta > 0:
            lines.append(f"📥 <b>{name}</b> received {fmt_amount(delta)} <b>{sym}</b>")
        else:
            lines.append(f"📤 <b>{name}</b> sent {fmt_amount(delta)} <b>{sym}</b>")

    if not tokens and abs(sol) >= MIN_SOL:
        verb = "received" if sol > 0 else "sent"
        lines.append(f"{'📥' if sol > 0 else '📤'} <b>{name}</b> {verb} {fmt_amount(sol)} SOL")

    if not lines:
        return None
    return "\n".join(lines) + f"\n{links}"


# ---------- Background checker ----------

async def check_wallets(app: Application) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            for address, info in list(wallets.items()):
                if not info["chats"]:
                    continue
                try:
                    params = {"limit": 10}
                    if info.get("last_sig"):
                        params["until"] = info["last_sig"]
                    sigs = await rpc(client, "getSignaturesForAddress", [address, params])
                    if not sigs:
                        continue
                    info["last_sig"] = sigs[0]["signature"]
                    save_data()

                    for s in reversed(sigs):  # oldest first
                        if s.get("err") is not None:
                            continue
                        tx = await rpc(client, "getTransaction", [
                            s["signature"],
                            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                        ])
                        for chat_id, label in info["chats"].items():
                            text = await describe(client, tx, address, label, s["signature"])
                            if text:
                                try:
                                    await app.bot.send_message(
                                        int(chat_id), text, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True,
                                    )
                                except Exception as e:  # e.g. bot removed from the group
                                    log.warning("Sending to %s failed: %s", chat_id, e)
                except Exception as e:
                    log.warning("Checking %s failed: %s", short(address), e)
                await asyncio.sleep(1)  # be gentle with the public RPC


# ---------- Commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👀 Solana Wallet Tracker\n\n"
        "I post a message here whenever a tracked wallet buys or sells a token.\n\n"
        "/track <address> [label] - track a wallet\n"
        "/untrack <address or label> - stop tracking\n"
        "/wallets - list tracked wallets\n\n"
        "Example: /track 5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1 whale"
    )


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /track <address> [label]")
        return

    address = context.args[0]
    label = " ".join(context.args[1:])[:30]
    if not ADDRESS_RE.match(address):
        await update.message.reply_text("That doesn't look like a Solana address.")
        return

    chat_id = str(update.effective_chat.id)
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            sigs = await rpc(client, "getSignaturesForAddress", [address, {"limit": 1}])
        except Exception:
            await update.message.reply_text("Couldn't reach Solana right now, please try again shortly.")
            return

    info = wallets.setdefault(address, {"last_sig": None, "chats": {}})
    if info["last_sig"] is None and sigs:
        info["last_sig"] = sigs[0]["signature"]  # only alert on new activity, not history
    info["chats"][chat_id] = label
    save_data()

    await update.message.reply_text(
        f"✅ Tracking {label or short(address)} ({short(address)}).\n"
        f"I'll post here when it buys or sells."
    )


async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /untrack <address or label>")
        return

    query = " ".join(context.args).lower()
    chat_id = str(update.effective_chat.id)
    for address, info in list(wallets.items()):
        label = info["chats"].get(chat_id)
        if label is not None and query in (address.lower(), label.lower()):
            del info["chats"][chat_id]
            if not info["chats"]:
                del wallets[address]
            save_data()
            await update.message.reply_text(f"Stopped tracking {label or short(address)}.")
            return
    await update.message.reply_text("That wallet isn't tracked in this chat. See /wallets")


async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    own = [(a, i["chats"][chat_id]) for a, i in wallets.items() if chat_id in i["chats"]]
    if not own:
        await update.message.reply_text("No wallets tracked here yet. Use /track <address>")
        return
    lines = [f"{n}. {label or '(no label)'} - {short(a)}" for n, (a, label) in enumerate(own, 1)]
    await update.message.reply_text("Tracked wallets:\n" + "\n".join(lines))


async def post_init(app: Application) -> None:
    app.bot_data["checker"] = asyncio.create_task(check_wallets(app))


def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("untrack", untrack))
    app.add_handler(CommandHandler("wallets", list_wallets))
    app.run_polling()


if __name__ == "__main__":
    main()
