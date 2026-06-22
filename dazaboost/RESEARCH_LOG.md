# Dazaboost MLB → Kalshi betting research log

Chronological record of hypotheses tested and what was found, so future sessions don't re-derive.
**TL;DR of the whole arc:** the Dazaboost model has one real, validated edge — its **contrarian
underdog moneyline picks beat the Kalshi closing line by ~9 points (+15.6% ROI)**. That edge is
**pre-game only**; once a game is underway the live Kalshi price is efficient. Everything else
(spread, totals, favorites, in-game "buy the dip") showed no usable edge.

---

## The data
- `data/mlb-2026.06.18.json` — 396 MLB games (394 usable), **2026-05-19 → 06-17**, output of the
  Dazaboost model. Each game's `bettingSummary` has predicted/actual winner, spread, total, and the
  casino lines. The model makes 3 calls per game: **winner, spread, total (O/U)**.
- Dazaboost live predictions API (no key): `https://www.dazaboost.ai/api/mlb/games?season=2026&seasonType=Regular%20Season&week=N`
- Kalshi market data is **public** (no auth): host `https://api.elections.kalshi.com`, MLB series
  `KXMLBGAME`. Authenticated endpoints (balance/orders) need RSA request signing.

## Key data gotchas (cost real debugging time — read before touching the feeds)
- **`casinoWinner` in the live Dazaboost feed mirrors `predictedWinner`** (identical every game) —
  useless for detecting contrarian picks. Use the **spread sign** instead: home-reference,
  `casinoSpread < 0` ⇒ home favored; contrarian dog = `sign(predictedSpread) == sign(casinoSpread)`.
- **`gameTimeEpoch` is the true UTC first pitch**; the field named `gameTimeEpochUtc` is mislabeled
  naive local time. Use `gameTimeEpoch`.
- **Casino lines post day-of** — future games have `casinoSpread = null` (can't screen until ~game day).
- Kalshi candle **volume field is `volume_fp`** (not `volume`); prices are in `*_dollars` fields.
- Kalshi ticker format: `KXMLBGAME-{YY}{MON}{DD}{HHMM-ET}{AWAY}{HOME}-{TEAM_ABBR}` (times are ET).
- The Kalshi key in `.env` (`api_key_kalshi`) is the **key ID (UUID)**; the RSA **private key** is
  the `.pem` at `KALSHI_PRIVATE_KEY_PATH` (`/Users/jake/Dropbox/keys/kalshi.pem`).
- All casino spreads in the dataset are ±1.5 (run line); zero pushes in the sample.

---

## Hypotheses & results (chronological)

**H1 — How good is the model, and at what? (RandomForest meta-eval)** → `mlb_rf_analysis.ipynb`
Raw accuracy: Winner **57.4%**, Spread (model's own flag) 59.4%, **Total O/U 46.2%** (below a coin
flip — systematic "Under" bias, predicted Under 83% of the time). RF meta-classifier (predict if a
call is right) precision: Spread 0.68, Winner 0.62, Total 0.45. Top features everywhere =
`total_vs_casino`, `casinoTotal`, `spread_vs_casino` → **calls that hug the market line are reliable;
big departures are where it breaks.**

**H2 — Re-grade spread vs the actual casino line (ATS), not the model's own flag.**
Spread-ATS raw accuracy drops to **53.0%** (beating the real ±1.5 line is much harder). RF lifts to
0.59 acc / 0.62 prec.

**H3 — Cherry-pick: where is the model more accurate?** (user asked re: predSpread<0 & casinoSpread>0)
That specific slice is the **worst** (39.7% ATS). Best spread slices ("disagree-on-favorite" /
"hug line") hit **67%** — but that **equals mechanically betting underdogs** (~61% always-bet-dog), so
it's run-line structure, **not skill**. Winner pick genuinely beats always-pick-favorite (57.3% vs
53.5%, **+3.8 pts**), edge widest in close-line games. Totals: no exploitable slice.

**H4 — Does bigger contrarian conviction → higher dog win rate?** No. Peaks at a **moderate 2–4 run**
disagreement (~59%), **regresses to a coin flip (52.6%) at ≥4 runs**. Extreme contrarian conviction =
model error, not insight. → don't up-stake the boldest dog calls.

**H5 — Kalshi framing.** On a prediction market, EV = (model win-prob − price − fee), not raw
accuracy. The exploitable spot: **model picks the casino underdog → wins 56.4%, market prices ~46¢**.

**H6 — Tier 1: real Kalshi closing prices + ROI backtest** → `kalshi_pull.py` → `data/kalshi_merged.csv`
Pulled closing-line mid (last bid/ask candle before first pitch) for all 394 games. Market is
well-calibrated. Model edge over market: ALL +3.7 pts, FAVORITES +1.5, **DOGS +9.3**. Backtest ROI
(buy at close, net of fees): **Underdogs +15.6%** (won 56.4%), **moderate-conv dogs +20.6%**,
**Favorites −0.9%** (no edge). 95% ROI CI ≈ [−4%, +35%] — **economically large but not yet
statistically conclusive** (110 dog bets, 30 days). Validated constants: `P_DOG=0.564`,
`P_DOG_MODCONV=0.586` (conviction ≤4).

**H7 — $1000 bankroll, 30-day compounding sim.** Contrarian dogs ¼-Kelly → **~$1,880 (+88%)**, 28%
max drawdown. In-sample/optimistic (same month that defined the edge, Kelly fed the in-sample
win-rate). Treat as best-case ceiling.

**H8 — Does Kalshi misprice near-locks (high-probability)?** No usable answer: **MLB moneyline prices
never exceed ~80¢** (high-variance sport), and where data exists the market is well-calibrated. The
favorite–longshot bias runs the *opposite* way (longshots overpriced) — consistent with our edge
being in *underpriced underdogs*.

**H9 — How often does a team trade ≤3¢ then win?** → `comeback_scan.py` → `data/comeback.csv`
(1-min candles, full game window, 1766 settled sides). Comeback rates: ≤1¢→1.1%, **≤3¢→3.6%**,
≤5¢→6.7%, ≤10¢→12%. **Rate ≈ price → market well-calibrated, not tradeable** after fees.

**H10 — Do comebacks improve when the model picked the dog?** Suggestively yes: at ≤3¢ model-dogs
came back **9.4% vs 3.6%** baseline (~2.6×); ≤10¢ 17.2% vs 12%. But **tiny sample** (5 wins/53 at
≤3¢), CI overlaps baseline.

**H11 — Multi-dim surface: innings-left + expected dog scoring → high win rate?** → `comeback_state.py`
(joins MLB play-by-play, `statsapi.mlb.com`). ⚠️ **First attempt had look-ahead bias**: conditioning
on each game's *global price minimum* embeds the outcome (winners bottom early, losers bottom at the
end) → produced a fake "run_surplus ≥ 0 → 100% win" surface. **Corrected** with a tradeable
**first-crossing** trigger → the surface **collapses**; only the obvious deficit gradient remains
(already priced). Lesson: always evaluate at a real-time decision point, never the global min.

**H12 — Empirical win-expectancy baseline + decisive dog overlay** → `build_we.py` →
`data/win_expectancy.csv` (WE(inning, deficit) from **1,238 games** of PBP; textbook-correct table).
**Decisive test:** model-dogs at first ≤10¢ crossing won **17.6%** vs the WE baseline **17.2%** for
the same (inning, deficit) state → **+0.5 pts ≈ ZERO edge**. **Conclusion: the model's edge is
pre-game only.** Once the game is underway the live Kalshi price already equals win expectancy; there
is **no in-game "buy the dip" strategy.**

---

## The validated strategy (what to actually do)
Bet **moneyline only**, **contrarian underdogs only** (model pick ≠ casino favorite by spread sign),
when `model_win_prob (0.564, or 0.586 if conviction ≤4) > Kalshi ask + fee`. Size at **¼-Kelly**.
Skip favorites (no edge) and totals/spreads. Enter at/near the closing line with **limit orders**
(thin books). Paper-trade / track live until ≥50 settled bets confirm the edge holds out-of-sample.

## Production tooling
- **`mlb_value_bets.py`** — the app. Modes: default daily card · `--pregame-window N` (poll, alert
  each game once ~N min before first pitch, dedupe `data/alerted.json`, log to `data/bet_log.csv`) ·
  `--preview` (morning full slate + CT game times) · `--settle` (fill outcomes from Kalshi
  settlements, print live ROI vs backtest) · `--dry-run` (no writes/Slack) · `--slack`/`--slack-always`.
  Bankroll defaults to **live Kalshi balance** (signed RSA request; `--bankroll` overrides). Slack
  posts **% of bankroll only**, no dollar amounts.
- **`.env`**: `api_key_kalshi` (key ID), `KALSHI_PRIVATE_KEY_PATH`, `SLACK_WEBHOOK_URL` (red-team-
  investing workspace incoming webhook). Run with the venv python
  `/Users/jake/Dropbox/sbx/notebook/venv/bin/python3` (has `cryptography`).
- **launchd jobs** (`*.plist`, also installed in `~/Library/LaunchAgents/`):
  `ai.dazaboost.mlbpreview` (9am CT preview → Slack) and `ai.dazaboost.mlbvaluebets` (every 10 min
  pre-game poller). ⚠️ Runs on the laptop — **misses windows while the Mac is asleep**; an always-on
  host or the cloud routine would fix it.

## Files
| File | What |
|---|---|
| `mlb_rf_analysis.ipynb` / `.py` | H1–H4: model skill, RF meta-eval, cherry-pick, conviction |
| `kalshi_pull.py` → `data/kalshi_merged.csv` | H6: closing-line prices + ROI backtest |
| `comeback_scan.py` → `data/comeback.csv` (+ `comeback_cache/`) | H9: ≤X¢ comeback rates |
| `comeback_state.py` → `data/comeback_state.csv` | H11: PBP-joined live state at dip |
| `build_we.py` → `data/win_expectancy.csv` (+ `pbp_cache/`, `sched_cache/`) | H12: win-expectancy table |
| `mlb_value_bets.py` | production screener/alerter/settler |

## Open / next
- Validate the dog edge **out-of-sample** (live `bet_log.csv` + `--settle`) — the one thing the
  analysis can't settle. Need ≥50 settled bets / multiple months to tighten the ROI CI.
- Optional Tier 2/3: calibrate a true P(win) (isotonic), add starting-pitcher/bullpen/park features,
  re-run the same backtest. Framework already wired to `kalshi_merged.csv`.
- Auto-execution on Kalshi is feasible (same signed-request scheme as balance) but deliberately not
  built — edge isn't proven; keep a human in the loop or paper-trade first.
