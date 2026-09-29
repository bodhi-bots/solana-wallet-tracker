# Solana Wallet Tracker for Telegram

A Telegram bot that watches Solana wallets and posts an alert in your chat or group the moment a tracked wallet buys, sells or transfers a token.

Built for crypto communities that want to follow whales, dev wallets, KOLs or their own treasury without refreshing Solscan all day.

## Example alerts

```
🟢 whale bought 1.23M BONK for 0.5 SOL · Chart
Tx · Wallet

🔴 dev wallet sold 500,000 BONK for 0.8 SOL · Chart
Tx · Wallet

📥 treasury received 25 SOL
Tx · Wallet
```

Every alert links to the transaction and wallet on Solscan and the token chart on DexScreener.

## Features

- Track multiple wallets per chat, each with its own label
- Detects buys, sells and incoming/outgoing transfers
- Looks up token tickers automatically
- Works in private chats and groups
- Only alerts on new activity, not the wallet's history
- Tracked wallets are saved and survive restarts
- Wrapped SOL is counted as SOL, dust and failed transactions are ignored

## Commands

| Command | What it does |
|---|---|
| `/start` | Show help |
| `/track <address> [label]` | Start tracking a wallet |
| `/untrack <address or label>` | Stop tracking a wallet |
| `/wallets` | List tracked wallets in this chat |

## Setup

```
pip install -r requirements.txt
```

Create a bot with [@BotFather](https://t.me/BotFather) and set the token as an environment variable:

```
# Windows (PowerShell)
$env:TELEGRAM_TOKEN="your_token"
python wallettracker.py
```

```
# Mac/Linux
export TELEGRAM_TOKEN="your_token"
python wallettracker.py
```

### RPC

By default the bot uses the public Solana RPC, which is rate-limited. For many or very active wallets, use a free RPC key (e.g. from Helius or QuickNode):

```
export SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=YOUR_KEY"
```

## Tech

- Python 3.10+
- [python-telegram-bot](https://python-telegram-bot.org/) (async)
- Solana JSON-RPC (`getSignaturesForAddress`, `getTransaction`)
- Token data from the [DexScreener API](https://docs.dexscreener.com/)

## Custom crypto bots

Need buy alerts for your token, a wallet tracker for your community, or price commands for your group? Get in touch via GitHub.
