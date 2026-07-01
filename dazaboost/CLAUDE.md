# CLAUDE.md — dazaboost

This project analyzes the **Dazaboost MLB prediction model** and turns its one validated edge into a
**Kalshi** betting workflow.

## 📓 Read this first: `RESEARCH_LOG.md`
A chronological log of every hypothesis tested (H1–H12), what was found, the dead ends, and the data
gotchas that cost real debugging time. **Read it before starting new analysis** — it will save you
from re-deriving results or repeating the look-ahead-bias mistake (H11).

**One-line summary:** the model's only validated edge is its **contrarian-underdog moneyline picks**,
which beat the Kalshi **closing line** by ~9 pts (+15.6% ROI). The edge is **pre-game only** — once a
game is live, the Kalshi price is efficient. Spread, totals, favorites, and in-game "buy the dip" all
showed no usable edge.

## Environment
- Now runs on an **always-on Linux EC2 host** (`/home/ec2-user/dazaboost/dazaboost`), migrated off
  the Mac so scheduled jobs don't miss windows while it sleeps.
- Run Python with the venv: `/home/ec2-user/dazaboost/venv/bin/python3` (has `cryptography`,
  `pandas`, `sklearn`). The app itself is stdlib + `cryptography`.
- Secrets in `.env`: `api_key_kalshi` (Kalshi key **ID**), `KALSHI_PRIVATE_KEY_PATH`
  (`~/.keys/kalshi.pem`, for signed balance/orders — keep the `~`, the code uses `expanduser`),
  `SLACK_WEBHOOK_URL` (red-team-investing workspace).
- **Multiple Kalshi accounts**: `--live` places each qualifying bet on *every* configured account,
  each sized off its **own** live balance. The primary account uses `api_key_kalshi` /
  `KALSHI_PRIVATE_KEY_PATH`; additional accounts are auto-discovered from any
  `api_key_kalshi_<name>` + `KALSHI_PRIVATE_KEY_PATH_<name>` pair in `.env` (e.g. `satish` →
  `~/.keys/kalshi-satish.pem`). Add another account by adding another pair — no code change. Each
  account gets its own per-account canary sentinel (`data/live_test_done_<name>.json`) and a
  distinct `client_order_id` namespace. Only the primary account's order is stamped into the bet
  log; `--settle` resolves win/loss by game outcome, which applies to all accounts.
- Kalshi market data is public (no auth); Dazaboost games API is public.

## Main entry point: `mlb_value_bets.py`
Daily Kalshi value-bet screener + live trader. Modes: default daily card · `--preview` (morning
slate + CT times) · `--pregame-window N` (poll/alert each game ~N min pre-game) · `--live`
(REAL orders for qualifying dogs; `--live-test` = 1-contract canary first) · `--settle` (live ROI
vs backtest) · `--comeback-watch` (PAPER-only in-game dip logger, see RESEARCH_LOG) · `--dry-run` ·
`--slack`/`--slack-always`. Sizes off live Kalshi balance (signed RSA); Slack shows **% of bankroll
only**. Orders use the V2 endpoint (`external-api.kalshi.com /portfolio/events/orders`; `side:"bid"`=buy YES,
`side:"ask"`=sell YES; `client_order_id` must be alphanumeric/dash — no `@`; **prices must be whole
cents** — 0.965 is rejected `invalid_price`). After a live buy fills, a `--take-profit` (default 96¢)
resting SELL closes the position. Scheduling on Linux is **cron** (`crontab.linux`, install with
`crontab crontab.linux`; host TZ is America/Chicago): 9am preview · every-10-min live poller ·
every-2-min comeback paper-watch. The Mac `*.plist` launchd jobs (incl. `caffeinate`) are retired.

See `RESEARCH_LOG.md` → "Files" table for what every script/CSV is.
