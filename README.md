# Web3 Assistant

A personal AI assistant for Web3 project work, accessed through Telegram.

**Status: Phase 4+ (database, friends list, project memory, tasks).** Projects, notes, tasks with deadlines and a friends list work.
It cannot hold AI conversations yet (Phase 3). Nothing here costs money.

## Setup on Windows (one time)

Open a terminal in this folder, then:

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

(Skip the first command if you already created `.venv`.)
Your `.env` file holds your secrets. It is ignored by Git. Never share it.

## Run

```
.venv\Scripts\activate
python -m assistant.main
```

## First-time bot setup

1. Create a bot with @BotFather in Telegram and put its token in `.env` as `TELEGRAM_BOT_TOKEN=`.
2. Start the bot, then send `/myid` to it in Telegram.
3. Put the number it replies with in `.env` as `ADMIN_USER_IDS=`, stop the bot (Ctrl+C), and start it again.

Only run ONE copy of the bot at a time. Two copies using the same token will fight each other.

## Crypto research

Paste a token contract address (0x...) into the bot, or tap **Crypto Analysis** in `/menu`, or use
`/token <address> [base|bsc|eth|sol]`. It checks Base, BNB Chain, Ethereum and Solana with two free sources:
DexScreener (price, liquidity, volume) and GoPlus Security (contract checks). Results are provider-reported,
labelled, time-stamped, and never called "safe". GoPlus's Solana API is in beta. Research only: no wallets, no trading.
Terms: GoPlus asks for credit ("Powered by GoPlus") and does not allow using its data to earn revenue
without written permission. DexScreener forbids building a competing product. Re-read both before any public or paid use.

## Campaign briefs

Menu > Web3 Project Management > Campaign briefs. A guided form for objective, audience, strategy, activities,
timeline, responsibilities, resources, metrics, reporting schedule and risks.

## Tasks

Open `/menu` and tap **Web3 Project Management**. From there you can add tasks, set deadlines
(try "friday", "tomorrow", "in 3 days", "2026-10-20" or "20/10/2026", day first), assign people,
add notes and mark tasks done, all with buttons. Shortcuts: `/tasks`, `/task <text> by friday`, `/cancel`.
Deadlines use the `TIMEZONE` setting in `.env` (default Africa/Lagos).

## Giving friends access

Only you (the owner, in `ADMIN_USER_IDS`) can add people. Ask a friend to send `/myid` to the bot and
tell you the number, then send `/adduser <number>` to the bot yourself. `/removeuser <number>` turns
their access off (their saved projects are kept but locked). `/users` lists everyone.

## Where data is stored

By default in a local file, `data/assistant.db` (ignored by Git). Never share it: it holds your
friends' projects. To use PostgreSQL later (for example Supabase), install the extra driver with
`pip install -e ".[postgres]"` and change `DATABASE_URL` in `.env`. Read the notes in the project plan
before doing this.

## Run the tests

```
python -m pytest
```

## Folder guide

- `src/assistant/config.py` - settings and limits
- `src/assistant/logging_setup.py` - logging that hides secrets
- `src/assistant/main.py` - starting point
- `src/assistant/interfaces/telegram/` - Telegram bot (Phase 2)
- `src/assistant/core/` - conversation engine (Phase 3)
- `src/assistant/providers/` - AI, vision, voice, image services (swappable)
- `src/assistant/memory`, `projects`, `tools`, `usage`, `db` - later phases
- `tests/` - automated tests (external services are always mocked)

## Troubleshooting

- **"Configuration error"**: a value in `.env` is wrong. Compare it to `.env.example`.
- **`pip` or `python` not found**: activate the environment first with `.venv\Scripts\activate`.
