# New Crypto Launch Alert Bot

Watches for brand-new token launches and pushes alerts to Telegram every 30 minutes, for free.

## What it watches
- **DexScreener** (official, free, no key) -- newly submitted token profiles and freshly
  boosted/promoted tokens. Since almost every launchpad's tokens eventually create a DEX
  liquidity pool, this indirectly catches launches from pump.fun, Four.meme, Moonshot, and others.
- **pump.fun** via **PumpPortal** (legitimate free third-party data feed, no key required) --
  real-time token creation events, sampled for ~25 seconds each run.
- **Clanker launches on Farcaster** via Neynar -- Clanker tokens are deployed through Farcaster
  casts, so this catches them at the source.

## What's NOT included, and why
Four.meme, GMGN, Moonshot, BullX, Photon, and Axiom do not publish official free public APIs.
The only ways to pull from them involve unofficial/reverse-engineered endpoints or paid
third-party wrapper services -- unstable, ToS gray-area, or not actually free. Rather than build
on something that can break or expose you to risk with no real benefit (since DexScreener already
catches the same launches once they hit a DEX), these were left out.

## ⚠️ Important
New token launches are extremely high risk. Most are worthless, many are scams or honeypots.
This bot does **no security or rug-check verification** -- it only tells you something new
launched. This is not financial advice. Always do your own research before acting on anything
this bot sends you.

## Setup (10 minutes)

### 1. Create the repo
Push this folder to a new GitHub repository.

### 2. Add secrets
Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value |
|---|---|
| `TELEGRAM_TOKEN` | bot token from @BotFather (can be the same bot as your other bot, or a new one) |
| `TELEGRAM_CHAT_ID` | your chat ID |
| `NEYNAR_API_KEY` | optional -- your free Neynar key, enables Clanker detection |

### 3. Enable Actions and test
Actions tab → enable workflows if prompted → "New Launch Alert Bot" → Run workflow.
Check Telegram after ~30-60 seconds.

## Tuning
- `PUMP_LISTEN_SECONDS` in the workflow controls how long each run listens to the pump.fun feed
  (longer = catches more launches per run, but runs take longer)
- `MAX_ALERTS_PER_RUN` in `main.py` caps how many alerts you get in one burst
- Change frequency by editing the `cron` schedule in `.github/workflows/alert.yml`
