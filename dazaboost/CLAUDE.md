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
- Run Python with the venv: `/Users/jake/Dropbox/sbx/notebook/venv/bin/python3` (has `cryptography`,
  `pandas`, `sklearn`). The app itself is stdlib + `cryptography`.
- Secrets in `.env`: `api_key_kalshi` (Kalshi key **ID**), `KALSHI_PRIVATE_KEY_PATH`
  (`/Users/jake/Dropbox/keys/kalshi.pem`, for signed balance/orders), `SLACK_WEBHOOK_URL`
  (red-team-investing workspace).
- Kalshi market data is public (no auth); Dazaboost games API is public.

## Main entry point: `mlb_value_bets.py`
Daily Kalshi value-bet screener. Modes: default daily card · `--preview` (morning slate + CT times) ·
`--pregame-window N` (poll/alert each game ~N min pre-game) · `--settle` (live ROI vs backtest) ·
`--dry-run` · `--slack`/`--slack-always`. Sizes off live Kalshi balance; Slack shows **% of bankroll
only**. Two launchd jobs (`*.plist`) drive it: 9am CT preview + every-10-min poller.

See `RESEARCH_LOG.md` → "Files" table for what every script/CSV is.
