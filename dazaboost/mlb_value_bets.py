#!/usr/bin/env python3
"""
mlb_value_bets.py — daily MLB value-bet screener.

Pulls today's Dazaboost model predictions, pulls live Kalshi moneyline prices, and applies the
edge validated in our analysis session:

  * Only CONTRARIAN-UNDERDOG moneyline picks have a real edge. In a 396-game backtest against
    Kalshi *closing* lines they won 56.4% (+15.6% ROI), or 58.6% (+20.6% ROI) when the model's
    disagreement with the line was a moderate <= 4 runs. FAVORITE picks showed no edge
    (-0.9% ROI) and are never bet.
  * "Contrarian dog" is decided by SPREAD SIGN, home-reference: casinoSpread < 0 => home favored.
    (The feed's `casinoWinner` field is unreliable — it just mirrors `predictedWinner`.)
  * A bet is placed only when model win-prob > live Kalshi ask + fee (positive net EV), and is
    sized at fractional Kelly.

Usage:
  python mlb_value_bets.py                 # today (US/Eastern), $1000 bankroll, 1/4 Kelly
  python mlb_value_bets.py --date 20260618 --bankroll 2500 --kelly 0.25
  python mlb_value_bets.py --week 13        # force the Dazaboost week param

No API key needed: Dazaboost's games endpoint and Kalshi's market-data endpoints are public reads.
"""
import argparse, json, math, os, sys, time, urllib.request, urllib.error
from datetime import date, datetime, timezone
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
    CENTRAL = ZoneInfo("America/Chicago")
except Exception:
    EASTERN = CENTRAL = None

# ---------------------------------------------------------------- config
DAZABOOST_URL = ("https://www.dazaboost.ai/api/mlb/games"
                 "?season={season}&seasonType=Regular%20Season&week={week}")
KALSHI_MKTS = ("https://api.elections.kalshi.com/trade-api/v2/markets"
               "?series_ticker=KXMLBGAME&status=open&limit=1000")
# Week 13 of 2026 is the Thu-Wed week starting 2026-06-18 (observed in the feed). Anchor off it.
WEEK_ANCHOR_DATE, WEEK_ANCHOR_NUM, SEASON = date(2026, 6, 18), 13, 2026

# validated win-rates (from the backtest)
P_DOG, P_DOG_MODCONV, MODCONV_RUNS = 0.564, 0.586, 4.0

ABBR2TEAM = {"ATH":"Oakland Athletics","ATL":"Atlanta Braves","AZ":"Arizona Diamondbacks","BAL":"Baltimore Orioles","BOS":"Boston Red Sox","CHC":"Chicago Cubs","CIN":"Cincinnati Reds","CLE":"Cleveland Guardians","COL":"Colorado Rockies","CWS":"Chicago White Sox","DET":"Detroit Tigers","HOU":"Houston Astros","KC":"Kansas City Royals","LAA":"Los Angeles Angels","LAD":"Los Angeles Dodgers","MIA":"Miami Marlins","MIL":"Milwaukee Brewers","MIN":"Minnesota Twins","NYM":"New York Mets","NYY":"New York Yankees","PHI":"Philadelphia Phillies","PIT":"Pittsburgh Pirates","SD":"San Diego Padres","SEA":"Seattle Mariners","SF":"San Francisco Giants","STL":"St. Louis Cardinals","TB":"Tampa Bay Rays","TEX":"Texas Rangers","TOR":"Toronto Blue Jays","WSH":"Washington Nationals"}
TEAM2ABBR = {v: k for k, v in ABBR2TEAM.items()}
MON3 = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- helpers
def http_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def load_env(path=".env"):
    """Minimal .env reader (KEY = 'value'); returns dict, ignores missing file."""
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'").strip('"')
    return env

def post_to_slack(webhook_url, text):
    """POST a message to a Slack Incoming Webhook. Returns True on success."""
    data = json.dumps({"text": text}).encode()
    req = urllib.request.Request(webhook_url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode() == "ok"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  ! Slack post failed: {e}", file=sys.stderr)
        return False

def build_slack_message(d, week, bets, bankroll, kelly_frac):
    """Format the bet card for Slack (mrkdwn). Sizing shown as % of bankroll only — no dollar amounts."""
    head = f":baseball: *MLB value bets — {d.isoformat()}* (week {week}, {int(kelly_frac*100)}% Kelly)"
    if not bets:
        return head + "\n_No qualifying contrarian-dog bets._"
    lines = [head, f"*{len(bets)} qualifying play(s):*"]
    total_pct = 0.0
    for x in bets:
        pct = x["kelly"] * 100
        total_pct += pct
        lines.append(
            f"• *BUY {x['pick']}* @ {int(round(x['ask']*100))}¢  ({x['matchup']})\n"
            f"    edge {(x['p']-x['ask'])*100:+.1f} pts · EV {x['ev']*100:+.1f}¢/contract · "
            f"size *{pct:.1f}% of bankroll* · limit @ {int(round(x['ask']*100))}¢")
    lines.append(f"_Total size: {total_pct:.1f}% of bankroll. Limit orders only._")
    return "\n".join(lines)


# ---------------------------------------------------------------- Kalshi authenticated balance
def kalshi_private_key(env):
    """Load the RSA private key from KALSHI_PRIVATE_KEY_PATH (a .pem file) or KALSHI_PRIVATE_KEY (\\n-escaped)."""
    pem = None
    path = env.get("KALSHI_PRIVATE_KEY_PATH") or os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if path and os.path.exists(os.path.expanduser(path)):
        pem = open(os.path.expanduser(path)).read()
    else:
        raw = env.get("KALSHI_PRIVATE_KEY") or os.environ.get("KALSHI_PRIVATE_KEY")
        if raw:
            pem = raw.replace("\\n", "\n")
    if not pem:
        return None
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem.encode(), password=None)

def kalshi_signed(method, path, env, body=None, host="https://api.elections.kalshi.com"):
    """Signed Kalshi request (GET/POST). `path` includes /trade-api/v2/... Returns parsed JSON.
    Raises on failure (callers decide how to handle)."""
    key_id = env.get("api_key_kalshi") or os.environ.get("api_key_kalshi")
    pk = kalshi_private_key(env)
    if not key_id or pk is None:
        raise RuntimeError("missing Kalshi key id or private key")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    import base64
    ts = str(int(time.time() * 1000))
    sign_path = path.split("?", 1)[0]                      # Kalshi signs the path WITHOUT query string
    sig = base64.b64encode(pk.sign(
        (ts + method + sign_path).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())).decode()
    headers = {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-SIGNATURE": sig,
               "KALSHI-ACCESS-TIMESTAMP": ts, "Accept": "application/json"}
    url = host + path
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def kalshi_balance_dollars(env):
    """Signed GET /portfolio/balance. Returns dollars (float) or None if unavailable."""
    try:
        d = kalshi_signed("GET", "/trade-api/v2/portfolio/balance", env)
    except Exception as e:
        print(f"  ! Kalshi balance fetch failed: {e}", file=sys.stderr); return None
    cents = d.get("balance")
    return cents / 100.0 if cents is not None else None

def get_kalshi_order(env, order_id):
    """Fetch a single order to report its fill status. Returns dict or None."""
    try:
        d = kalshi_signed("GET", f"/trade-api/v2/portfolio/orders/{order_id}", env)
        return d.get("order", d)
    except Exception:
        return None

KALSHI_ORDER_HOST = "https://external-api.kalshi.com"
KALSHI_ORDER_PATH = "/trade-api/v2/portfolio/events/orders"   # V2 create-order

def place_kalshi_order(env, ticker, price_dollars, count, client_order_id):
    """Place a LIMIT BUY-YES order via the V2 endpoint. BUY YES = side 'bid'.
    Limit at `price_dollars`, immediate-or-cancel (fill at our price now or skip — no stale rests).
    Idempotent via client_order_id. Returns (ok, info)."""
    body = {"ticker": ticker, "side": "bid",
            "count": f"{int(count)}.00",                 # fixed-point string, 2 dp
            "price": f"{price_dollars:.4f}",             # USD fixed-point string
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": client_order_id}
    try:
        d = kalshi_signed("POST", KALSHI_ORDER_PATH, env, body=body, host=KALSHI_ORDER_HOST)
        order = d.get("order", d)
        return True, {"order_id": order.get("order_id") or order.get("id"),
                      "status": order.get("status"),
                      "fill_count": order.get("fill_count") or order.get("filled_count")}
    except urllib.error.HTTPError as e:
        return False, {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return False, {"error": str(e)}

def resolve_bankroll(args):
    """Bankroll = --bankroll override, else live Kalshi balance, else $1000 fallback."""
    if args.bankroll is not None:
        return float(args.bankroll), "override"
    bal = kalshi_balance_dollars(load_env())
    if bal is not None:
        return bal, "live Kalshi balance"
    return 1000.0, "fallback $1000 (no live balance — add KALSHI_PRIVATE_KEY_PATH to .env)"

def kalshi_fee(price):
    """Kalshi trading fee per contract (charged on entry), in dollars."""
    return math.ceil(0.07 * price * (1 - price) * 100) / 100

def week_for(d):
    return WEEK_ANCHOR_NUM + math.floor((d - WEEK_ANCHOR_DATE).days / 7)

def fetch_dazaboost(week):
    url = DAZABOOST_URL.format(season=SEASON, week=week)
    hdrs = {"Accept": "*/*", "User-Agent": UA, "Referer": "https://www.dazaboost.ai/leagues/mlb/games"}
    return http_json(url, hdrs)

def games_for_date(datestr, week):
    """Fetch the computed week; fall back to neighbors if today's slate isn't there."""
    for w in (week, week - 1, week + 1):
        try:
            games = fetch_dazaboost(w)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  ! Dazaboost fetch failed for week {w}: {e}", file=sys.stderr); continue
        todays = [g for g in games if g.get("gameDate") == datestr]
        if todays:
            return todays, w
    return [], week

def casino_favorite(g):
    cs = g.get("casinoSpread")
    if cs is None: return None
    return g["homeTeamName"] if cs < 0 else g["awayTeamName"]

def predicted_winner(g):
    ps = g.get("predictedSpread")
    if ps is None or ps == 0: return None
    return g["homeTeamName"] if ps > 0 else g["awayTeamName"]

def datecode_for(g):
    """Kalshi ticker date code from the Dazaboost gameDate, e.g. 20260618 -> 26JUN18."""
    gd = g["gameDate"]
    return f"{gd[2:4]}{MON3[int(gd[4:6])]}{int(gd[6:8]):02d}"

def market_for(kmarkets, abbr, datecode):
    """Return the Kalshi market dict for this team+date, or None."""
    if not abbr: return None
    for m in kmarkets:
        if datecode in m["ticker"] and m["ticker"].rsplit("-", 1)[-1] == abbr:
            return m
    return None

def ask_for(kmarkets, abbr, datecode):
    m = market_for(kmarkets, abbr, datecode)
    if not m: return None
    a = m.get("yes_ask_dollars"); return float(a) if a else None

def evaluate_game(g, kmarkets, kelly_frac, min_edge_cents):
    """Classify one game and, if it's a +EV contrarian-dog, compute bet sizing.
    Returns a dict with 'kind' in {no_line,no_pick,favorite,dog_unpriced,dog_noev,bet}."""
    pick = predicted_winner(g)
    r = {"matchup": f"{g['awayTeamName']} @ {g['homeTeamName']}", "pick": pick, "game": g}
    if g.get("casinoSpread") is None: r["kind"] = "no_line"; return r
    if pick is None:                   r["kind"] = "no_pick"; return r
    if casino_favorite(g) == pick:     r["kind"] = "favorite"; return r
    conv = abs(g["predictedSpread"] + g["casinoSpread"])
    p = P_DOG_MODCONV if conv <= MODCONV_RUNS else P_DOG
    mkt = market_for(kmarkets, TEAM2ABBR.get(pick), datecode_for(g))
    ask = float(mkt["yes_ask_dollars"]) if mkt and mkt.get("yes_ask_dollars") else None
    if ask is None: r.update(kind="dog_unpriced", p=p, conv=conv); return r
    r["ticker"] = mkt["ticker"]
    ev = p - ask - kalshi_fee(ask)
    b = (1 - ask) / ask
    kelly = max(0.0, (p * b - (1 - p)) / b) * kelly_frac
    r.update(kind=("bet" if ev * 100 > min_edge_cents else "dog_noev"),
             ask=ask, p=p, ev=ev, kelly=kelly, conv=conv)
    return r

def load_state(path):
    try: return set(json.load(open(path)))
    except Exception: return set()

def save_state(path, keys):
    try: json.dump(sorted(keys), open(path, "w"))
    except Exception as e: print(f"  ! could not write state {path}: {e}", file=sys.stderr)

def fetch_week_with_fallback(week):
    """Return (games_list, week_used), trying the computed week then neighbors."""
    for w in (week, week - 1, week + 1):
        try:
            games = fetch_dazaboost(w)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  ! Dazaboost fetch failed for week {w}: {e}", file=sys.stderr); continue
        if games: return games, w
    return [], week


# ---------------------------------------------------------------- results log
import csv
BET_LOG_COLS = ["logged_at_utc", "gameDate", "gameId", "matchup", "pick", "kalshi_abbr",
                "ask", "win_prob", "conviction", "ev_cents", "kelly_frac",
                "stake_dollars", "contracts", "status", "result", "pnl_dollars", "settled_at_utc",
                "order_id", "order_status"]

def _read_bet_log(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

def _write_bet_log(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BET_LOG_COLS); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in BET_LOG_COLS})

def log_bets(path, bets, bankroll, kelly_frac):
    """Append newly-fired bets as status=open, skipping games already logged."""
    rows = _read_bet_log(path)
    seen = {(r["gameDate"], r["gameId"]) for r in rows}
    now = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = 0
    for x in bets:
        g = x["game"]; key = (g["gameDate"], g["gameId"])
        if key in seen: continue
        stake = round(x["kelly"] * bankroll, 2)
        contracts = int(stake / x["ask"]) if x["ask"] else 0
        rows.append({"logged_at_utc": now, "gameDate": g["gameDate"], "gameId": g["gameId"],
                     "matchup": x["matchup"], "pick": x["pick"], "kalshi_abbr": TEAM2ABBR.get(x["pick"], ""),
                     "ask": f"{x['ask']:.4f}", "win_prob": f"{x['p']:.4f}", "conviction": f"{x['conv']:g}",
                     "ev_cents": f"{x['ev']*100:.1f}", "kelly_frac": f"{x['kelly']:.4f}",
                     "stake_dollars": f"{stake:.2f}", "contracts": contracts,
                     "status": "open", "result": "", "pnl_dollars": "", "settled_at_utc": ""})
        seen.add(key); added += 1
    if added: _write_bet_log(path, rows)
    return added

def fetch_settled_results():
    """Map (datecode, abbr) -> 'yes'/'no' for settled KXMLBGAME markets."""
    out, cursor, pages = {}, None, 0
    while pages < 4:
        url = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=settled&limit=1000"
        if cursor: url += f"&cursor={cursor}"
        d = http_json(url); pages += 1
        for m in d.get("markets", []):
            body, abbr = m["ticker"][len("KXMLBGAME")+1:].rsplit("-", 1)
            datecode = body[:7]                       # e.g. 26JUN18
            if m.get("result") in ("yes", "no"):
                out[(datecode, abbr)] = m["result"]
        cursor = d.get("cursor")
        if not cursor: break
    return out

def settle_and_report(path):
    rows = _read_bet_log(path)
    if not rows:
        print(f"No bets logged yet at {path}."); return
    results = fetch_settled_results()
    now = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    newly = 0
    for r in rows:
        if r.get("status") != "open": continue
        gd = r["gameDate"]; datecode = f"{gd[2:4]}{MON3[int(gd[4:6])]}{int(gd[6:8]):02d}"
        res = results.get((datecode, r["kalshi_abbr"]))
        if res is None: continue
        ask = float(r["ask"]); contracts = int(r["contracts"]); won = (res == "yes")
        pnl = contracts * ((1.0 if won else 0.0) - ask) - contracts * kalshi_fee(ask)
        r.update(status=("won" if won else "lost"), result=res,
                 pnl_dollars=f"{pnl:.2f}", settled_at_utc=now)
        newly += 1
    if newly: _write_bet_log(path, rows)

    settled = [r for r in rows if r.get("status") in ("won", "lost")]
    opn = [r for r in rows if r.get("status") == "open"]
    print(f"=== Bet log report ({path}) ===")
    print(f"  logged: {len(rows)} | settled: {len(settled)} (newly {newly}) | open: {len(opn)}")
    if settled:
        w = sum(1 for r in settled if r["status"] == "won")
        stake = sum(float(r["stake_dollars"]) for r in settled)
        pnl = sum(float(r["pnl_dollars"]) for r in settled)
        wr = w / len(settled)
        roi = (pnl / stake * 100) if stake else 0.0
        print(f"  record: {w}-{len(settled)-w}  ({wr:.3f} win rate)")
        print(f"  staked: ${stake:,.2f} | P&L: ${pnl:+,.2f} | ROI: {roi:+.1f}%")
        print(f"  backtest benchmark: 56.4% win, +15.6% ROI (contrarian dogs)")
    if opn:
        print(f"  awaiting result: " + ", ".join(f"{r['pick']} ({r['gameDate']})" for r in opn))


# ---------------------------------------------------------------- modes
def fmt_gametime(epoch):
    """UTC epoch -> local Central clock time like '7:05pm CT'."""
    if epoch is None: return "  ??:?? "
    tz = CENTRAL or timezone.utc
    dt = datetime.fromtimestamp(epoch, tz)
    return dt.strftime("%I:%M%p").lstrip("0").lower() + (" CT" if CENTRAL else " UTC")

KIND_TAG = {"bet": "🟢 DOG BET", "favorite": "favorite", "dog_unpriced": "dog (no price yet)",
            "dog_noev": "dog (-EV)", "no_line": "no casino line", "no_pick": "no pick"}

def build_preview_slack(d, rows, kelly_frac):
    """Morning preview: full slate with game times; highlights qualifying bets. % sizing only."""
    bets = [r for r in rows if r["kind"] == "bet"]
    head = (f":baseball: *MLB preview — {d.isoformat()}*  •  {len(rows)} games  •  "
            f"{len(bets)} qualifying bet(s)  ({int(kelly_frac*100)}% Kelly)")
    lines = [head]
    if bets:
        lines.append("*Expected bets (contrarian dogs):*")
        for r in bets:
            lines.append(f"• {fmt_gametime(r['start'])}  *BUY {r['pick']}* @ {int(round(r['ask']*100))}¢ "
                         f"({r['matchup']}) — edge {(r['p']-r['ask'])*100:+.1f} pts · size *{r['kelly']*100:.1f}% of bankroll*")
    lines.append("*Full slate:*")
    for r in rows:
        tag = KIND_TAG.get(r["kind"], r["kind"])
        lines.append(f"• {fmt_gametime(r['start'])}  {r['matchup']} — pick {r['pick'] or '?'} [{tag}]")
    lines.append("_Preview only — no bets placed. Each bet fires ~30 min before its game._")
    return "\n".join(lines)

def run_preview(args, kmarkets):
    """Full-slate morning preview: print + Slack, write nothing."""
    today = (datetime.now(EASTERN).date() if EASTERN else date.today())
    week = args.week or week_for(today)
    games, used_week = fetch_week_with_fallback(week)
    datestr = today.strftime("%Y%m%d")
    todays = [g for g in games if g.get("gameDate") == datestr and g.get("gameStatus") == "scheduled"]
    rows = []
    for g in todays:
        r = evaluate_game(g, kmarkets, args.kelly, args.min_edge_cents)
        try: r["start"] = int(float(g["gameTimeEpoch"]))
        except (KeyError, ValueError, TypeError): r["start"] = None
        rows.append(r)
    rows.sort(key=lambda r: r["start"] or 0)
    bets = [r for r in rows if r["kind"] == "bet"]

    print(f"=== MLB preview — {today.isoformat()} (week {used_week}) — "
          f"{len(rows)} games, {len(bets)} qualifying bet(s) ===")
    for r in rows:
        extra = (f"  @ {int(round(r['ask']*100))}c size {r['kelly']*100:.1f}%"
                 if r["kind"] == "bet" else "")
        print(f"  {fmt_gametime(r['start']):>10}  {r['matchup']:40s} pick {(r['pick'] or '?'):20s} [{KIND_TAG.get(r['kind'], r['kind'])}]{extra}")

    if args.slack or args.slack_always:
        webhook = os.environ.get("SLACK_WEBHOOK_URL") or load_env().get("SLACK_WEBHOOK_URL")
        if not webhook:
            print("  ! --slack set but SLACK_WEBHOOK_URL not found", file=sys.stderr)
        elif post_to_slack(webhook, build_preview_slack(today, rows, args.kelly)):
            print(f"  Posted preview to Slack ({len(rows)} games, {len(bets)} bets).")


LIVE_TEST_SENTINEL = "data/live_test_done.json"

def place_live_orders(args, bets):
    """Auto-submit LIMIT BUY YES orders for qualifying dog bets. Idempotent; halts on first error.
    Sizing: floor(stake/ask) contracts at the model's ask price (the price EV was computed on).

    --live-test canary: until the sentinel exists, the FIRST qualifying bet is placed at exactly
    1 contract, its fill is reported, the sentinel is written, and the run stops — proving the
    pipeline end-to-end. Afterwards (sentinel present) it reverts to normal full-size live betting."""
    env = load_env()
    if kalshi_private_key(env) is None:
        print("    ! live mode set but no Kalshi private key (KALSHI_PRIVATE_KEY_PATH) — NO orders placed",
              file=sys.stderr)
        return
    canary = getattr(args, "live_test", False) and not os.path.exists(LIVE_TEST_SENTINEL)
    for x in bets:
        ticker = x.get("ticker")
        if not ticker:
            print(f"    ! no Kalshi ticker for {x['pick']} — skipped", file=sys.stderr); continue
        stake = x["kelly"] * args.bankroll
        price = x["ask"]
        count = 1 if canary else (int(stake / price) if price > 0 else 0)
        if count < 1:
            print(f"    · {x['pick']}: stake ${stake:.2f} < 1 contract — skipped"); continue
        g = x["game"]
        safe_gid = g["gameId"].replace("@", "").replace("_", "-")   # Kalshi rejects '@' in client_order_id
        coid = f"daza-{safe_gid}-{TEAM2ABBR.get(x['pick'],'?')}"     # idempotent, alphanumeric+dash only
        cents = int(round(price * 100))
        tag = "🐤 CANARY 1-contract TEST" if canary else "✅ LIVE ORDER"
        ok, info = place_kalshi_order(env, ticker, price, count, coid)
        if ok:
            print(f"    {tag}: BUY {count} {ticker} @ {cents}c (${count*price:.2f}) "
                  f"order_id={info.get('order_id')} status={info.get('status')} "
                  f"filled={info.get('fill_count','?')}/{count}")
            _record_order(args.bet_log, g, x['pick'], info, count)
            if canary:                                     # one-shot: stop after the single test order
                json.dump({"order_id": info.get("order_id"), "ticker": ticker,
                           "placed_at": datetime.fromtimestamp(time.time(), timezone.utc).isoformat()},
                          open(LIVE_TEST_SENTINEL, "w"))
                print("    🐤 canary placed — full-size live betting active from the next qualifying bet.")
                break
        else:
            print(f"    ! ORDER FAILED for {ticker}: {info.get('error')} — HALTING further orders",
                  file=sys.stderr)
            break                                          # halt-on-error: don't keep firing

def _record_order(path, g, pick, info, count):
    """Stamp the matching bet-log row with the live order id/status. Leaves status='open'
    so --settle still resolves win/lost from Kalshi settlements later."""
    rows = _read_bet_log(path)
    for r in rows:
        if r["gameDate"] == g["gameDate"] and r["gameId"] == g["gameId"] and r["pick"] == pick:
            r["order_id"] = info.get("order_id", "")
            r["order_status"] = info.get("status", "")
            break
    _write_bet_log(path, rows)


def run_pregame(args, kmarkets):
    """Poll mode: alert each game once when it is within --pregame-window minutes of first pitch."""
    now = time.time()
    week = args.week or week_for(date.today())
    games, used_week = fetch_week_with_fallback(week)
    state_path = args.state_file
    state = load_state(state_path)

    in_window, new_bets, decided = [], [], 0
    for g in games:
        try:
            start = int(float(g["gameTimeEpoch"]))      # true UTC start (NOT gameTimeEpochUtc)
        except (KeyError, ValueError, TypeError):
            continue
        mins = (start - now) / 60.0
        if mins <= 0 or mins > args.pregame_window:      # not imminent (or already started)
            continue
        key = f"{g['gameDate']}:{g['gameId']}"
        if key in state:                                  # already handled this game
            continue
        r = evaluate_game(g, kmarkets, args.kelly, args.min_edge_cents)
        in_window.append((round(mins), r))
        if r["kind"] == "no_line":                        # line not posted yet -> retry next poll
            continue
        state.add(key); decided += 1                      # decision made; don't revisit
        if r["kind"] == "bet":
            new_bets.append(r)
    dry = getattr(args, "dry_run", False)
    if dry:
        print("    [DRY RUN] no state write, no bet log, no Slack, no orders")
    else:
        save_state(state_path, state)
        if new_bets:                                      # record fired bets for ROI tracking
            n = log_bets(args.bet_log, new_bets, args.bankroll, args.kelly)
            if n: print(f"    logged {n} bet(s) to {args.bet_log}")
        if (getattr(args, "live", False) or getattr(args, "live_test", False)) and new_bets:
            place_live_orders(args, new_bets)

    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    print(f"[{stamp}] pregame poll (window {args.pregame_window}m): "
          f"{len(in_window)} game(s) in window, {decided} decided, {len(new_bets)} qualifying bet(s)")
    for mins, r in in_window:
        print(f"    T-{mins:>3}m  {r['matchup']:38s} {r['kind']}"
              + (f"  pick {r['pick']} @ {int(round(r['ask']*100))}c EV {r['ev']*100:+.1f}c" if r.get("ask") else ""))

    if (args.slack or args.slack_always) and new_bets and not dry:
        webhook = os.environ.get("SLACK_WEBHOOK_URL") or load_env().get("SLACK_WEBHOOK_URL")
        if not webhook:
            print("  ! --slack set but SLACK_WEBHOOK_URL not found", file=sys.stderr)
        else:
            msg = build_slack_message(date.today(), used_week, new_bets, args.bankroll, args.kelly)
            if post_to_slack(webhook, msg):
                print(f"  Posted {len(new_bets)} bet(s) to Slack.")


# ---------------------------------------------------------------- screen
def main():
    ap = argparse.ArgumentParser(description="MLB value-bet screener (Dazaboost x Kalshi).")
    ap.add_argument("--date", help="YYYYMMDD (default: today, US/Eastern)")
    ap.add_argument("--week", type=int, help="override Dazaboost week param")
    ap.add_argument("--bankroll", type=float, default=None,
                    help="override bankroll; default = live Kalshi balance (falls back to $1000)")
    ap.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction (default 0.25)")
    ap.add_argument("--min-edge-cents", type=float, default=0.0, help="min net EV (cents/contract) to bet")
    ap.add_argument("--slack", action="store_true", help="post the card to Slack (webhook from .env SLACK_WEBHOOK_URL)")
    ap.add_argument("--slack-always", action="store_true", help="post to Slack even on no-bet days (implies --slack)")
    ap.add_argument("--pregame-window", type=int, metavar="MIN",
                    help="poll mode: alert each game once when it is within MIN minutes of first pitch")
    ap.add_argument("--state-file", default="data/alerted.json",
                    help="dedupe state for poll mode (default data/alerted.json)")
    ap.add_argument("--bet-log", default="data/bet_log.csv",
                    help="CSV of fired bets + outcomes (default data/bet_log.csv)")
    ap.add_argument("--settle", action="store_true",
                    help="fill outcomes for logged bets from Kalshi settlements and print live ROI")
    ap.add_argument("--dry-run", action="store_true",
                    help="poll mode: show what would happen but do NOT post Slack, write state, or log bets")
    ap.add_argument("--preview", action="store_true",
                    help="full-slate morning preview with game times (writes nothing); add --slack to post")
    ap.add_argument("--live", action="store_true",
                    help="REAL MONEY: auto-submit limit BUY orders on Kalshi for qualifying dog bets "
                         "(poll mode only). Idempotent; halts on first API error.")
    ap.add_argument("--live-test", action="store_true",
                    help="One-shot canary: place a single 1-contract real order on the next qualifying "
                         "dog, report the fill, then revert to full-size live betting (sentinel: "
                         "data/live_test_done.json — delete it to re-arm the canary).")
    args = ap.parse_args()
    if (args.live or args.live_test) and not getattr(args, "dry_run", False):
        print("    ⚠️  LIVE TRADING ENABLED — real Kalshi orders will be placed for qualifying bets")

    # Settle/report mode: update outcomes for logged bets and print realized ROI.
    if args.settle:
        settle_and_report(args.bet_log)
        return

    # Preview mode: full-slate morning heads-up with game times. Writes nothing; % sizing only.
    if args.preview:
        try:
            kmarkets = http_json(KALSHI_MKTS)["markets"]
        except Exception as e:
            print(f"  ! Kalshi fetch failed: {e}", file=sys.stderr); kmarkets = []
        run_preview(args, kmarkets)
        return

    # Resolve bankroll (live Kalshi balance unless --bankroll override) for any sizing mode.
    bankroll, src = resolve_bankroll(args)
    args.bankroll = bankroll
    print(f"    bankroll: ${bankroll:,.2f}  ({src})")

    # Pre-game poll mode: evaluate only imminent games, alert each once. (run every ~10 min)
    if args.pregame_window:
        try:
            kmarkets = http_json(KALSHI_MKTS)["markets"]
        except Exception as e:
            print(f"  ! Kalshi fetch failed: {e}", file=sys.stderr); kmarkets = []
        run_pregame(args, kmarkets)
        return

    today = (datetime.now(EASTERN).date() if EASTERN else date.today())
    if args.date:
        d = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        d = today
    datestr = d.strftime("%Y%m%d")
    datecode = f"26{MON3[d.month]}{d.day:02d}"   # Kalshi ticker date code
    week = args.week or week_for(d)

    print(f"=== MLB value bets — {d.isoformat()} (week {week}) ===")
    print(f"    bankroll ${args.bankroll:,.0f} | {int(args.kelly*100)}% Kelly | edge: contrarian dogs only\n")

    todays, used_week = games_for_date(datestr, week)
    if not todays:
        print("No games found for this date."); return

    # live Kalshi asks for the date
    try:
        kmarkets = http_json(KALSHI_MKTS)["markets"]
    except Exception as e:
        print(f"  ! Kalshi fetch failed: {e}", file=sys.stderr); kmarkets = []
    def live_ask(abbr):
        for m in kmarkets:
            if datecode in m["ticker"] and m["ticker"].rsplit("-", 1)[-1] == abbr:
                a = m.get("yes_ask_dollars"); return float(a) if a else None
        return None

    bettable = [g for g in todays if g.get("gameStatus") == "scheduled"]
    started = len(todays) - len(bettable)
    no_line = [g for g in bettable if g.get("casinoSpread") is None]
    print(f"{len(todays)} games today | {started} already started | "
          f"{len(bettable)} not started | {len(no_line)} awaiting casino line\n")

    bets, skipped_fav, dogs_unpriced = [], [], []
    for g in bettable:
        if g.get("casinoSpread") is None:
            continue
        pick = predicted_winner(g)
        if pick is None:
            continue
        matchup = f"{g['awayTeamName']} @ {g['homeTeamName']}"
        if casino_favorite(g) == pick:
            skipped_fav.append((matchup, pick)); continue          # favorite -> no edge
        conv = abs(g["predictedSpread"] + g["casinoSpread"])
        p = P_DOG_MODCONV if conv <= MODCONV_RUNS else P_DOG
        ask = live_ask(TEAM2ABBR.get(pick))
        if ask is None:
            dogs_unpriced.append((matchup, pick)); continue
        ev = p - ask - kalshi_fee(ask)                             # net $/contract
        b = (1 - ask) / ask
        kelly = max(0.0, (p * b - (1 - p)) / b) * args.kelly
        if ev * 100 > args.min_edge_cents:
            bets.append(dict(matchup=matchup, pick=pick, ask=ask, p=p, ev=ev, kelly=kelly, conv=conv))

    # ---- output ----
    if bets:
        bets.sort(key=lambda x: -x["ev"])
        print(f"BET CARD — {len(bets)} qualifying play(s):\n")
        for x in bets:
            stake = x["kelly"] * args.bankroll
            ctr = int(stake / x["ask"]) if x["ask"] else 0
            print(f"  ✅ BUY  {x['pick']}  ({x['matchup']})")
            print(f"        win-prob {x['p']:.3f}  vs  Kalshi ask {int(round(x['ask']*100))}c"
                  f"  ->  edge {(x['p']-x['ask'])*100:+.1f} pts, net EV {x['ev']*100:+.1f}c/contract")
            print(f"        stake {x['kelly']*100:.1f}% of bankroll = ${stake:,.0f}  (~{ctr} contracts)")
            print(f"        place a LIMIT order at {int(round(x['ask']*100))}c or better\n")
        total = sum(x["kelly"] for x in bets) * args.bankroll
        print(f"  Total recommended exposure: ${total:,.0f} ({total/args.bankroll*100:.1f}% of bankroll)")
    else:
        print("BET CARD — no qualifying bets today.")
        print("  (Our validated edge is contrarian underdogs at a +EV price; none today.)")

    if skipped_fav:
        print(f"\n  Skipped {len(skipped_fav)} favorite pick(s) — no validated edge:")
        for m, p in skipped_fav: print(f"    · {m}  (model: {p})")
    if dogs_unpriced:
        print(f"\n  {len(dogs_unpriced)} contrarian dog(s) with no live Kalshi price yet:")
        for m, p in dogs_unpriced: print(f"    · {m}  (model: {p})")

    # ---- Slack notification (red-team-investing workspace via Incoming Webhook) ----
    if args.slack or args.slack_always:
        webhook = os.environ.get("SLACK_WEBHOOK_URL") or load_env().get("SLACK_WEBHOOK_URL")
        if not webhook:
            print("\n  ! --slack set but SLACK_WEBHOOK_URL not found in env or .env", file=sys.stderr)
        elif bets or args.slack_always:
            msg = build_slack_message(d, used_week, bets, args.bankroll, args.kelly)
            if post_to_slack(webhook, msg):
                print(f"\n  Posted to Slack ({len(bets)} bet(s)).")
        else:
            print("\n  No bets — Slack not pinged (use --slack-always to post anyway).")


if __name__ == "__main__":
    main()
