"""
Tier 1: pull Kalshi closing-line prices for the games in the model dataset and merge.

Kalshi market-data endpoints are public (no auth). We only have the API key *ID* in .env
(the RSA private key needed for signed/trading endpoints is not present), but reads don't need it.
Output: data/kalshi_merged.csv  (one row per game: model prediction + Kalshi closing prices + result)
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXMLBGAME"
CACHE = "data/kalshi_cache"
os.makedirs(CACHE, exist_ok=True)
START_DATE, END_DATE = "2026-05-19", "2026-06-17"   # model dataset range

ABBR2TEAM = {
    "ATH":"Oakland Athletics","ATL":"Atlanta Braves","AZ":"Arizona Diamondbacks",
    "BAL":"Baltimore Orioles","BOS":"Boston Red Sox","CHC":"Chicago Cubs",
    "CIN":"Cincinnati Reds","CLE":"Cleveland Guardians","COL":"Colorado Rockies",
    "CWS":"Chicago White Sox","DET":"Detroit Tigers","HOU":"Houston Astros",
    "KC":"Kansas City Royals","LAA":"Los Angeles Angels","LAD":"Los Angeles Dodgers",
    "MIA":"Miami Marlins","MIL":"Milwaukee Brewers","MIN":"Minnesota Twins",
    "NYM":"New York Mets","NYY":"New York Yankees","PHI":"Philadelphia Phillies",
    "PIT":"Pittsburgh Pirates","SD":"San Diego Padres","SEA":"Seattle Mariners",
    "SF":"San Francisco Giants","STL":"St. Louis Cardinals","TB":"Tampa Bay Rays",
    "TEX":"Texas Rangers","TOR":"Toronto Blue Jays","WSH":"Washington Nationals",
}
MON = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if i == tries-1: raise
            time.sleep(1.5*(i+1))

def parse_ticker(ticker):
    """KXMLBGAME-26JUN172140PITATH-PIT -> (date 'YYYY-MM-DD', first_pitch_utc, abbr)"""
    body = ticker[len(SERIES)+1:]
    code, abbr = body.rsplit("-", 1)
    yy, mon, dd = int(code[:2]), MON[code[2:5]], int(code[5:7])
    hh, mm = int(code[7:9]), int(code[9:11])
    et = datetime(2000+yy, mon, dd, hh, mm, tzinfo=timezone(timedelta(hours=-4)))  # EDT
    fp = et.astimezone(timezone.utc)
    return f"{2000+yy:04d}-{mon:02d}-{dd:02d}", fp, abbr

# ---- 1. collect all settled markets in range ----
print("Fetching settled MLB markets ...")
markets, cursor, pages = [], None, 0
while True:
    url = f"{BASE}/markets?series_ticker={SERIES}&status=settled&limit=1000"
    if cursor: url += f"&cursor={cursor}"
    d = get(url); pages += 1
    markets += d.get("markets", [])
    cursor = d.get("cursor")
    if not cursor or pages > 12: break
print(f"  {len(markets)} settled markets across {pages} pages")

games = {}   # event_ticker -> {date, sides:{abbr:{...}}}
for m in markets:
    tk = m["ticker"]
    try: date, fp, abbr = parse_ticker(tk)
    except Exception: continue
    if not (START_DATE <= date <= END_DATE): continue
    ev = m["event_ticker"]
    g = games.setdefault(ev, {"date": date, "first_pitch": fp, "sides": {}})
    g["sides"][abbr] = {"ticker": tk, "result": m.get("result"),
                        "prev_price": m.get("previous_price_dollars")}
print(f"  {len(games)} games in {START_DATE}..{END_DATE}")

# ---- 2. closing-line price per market via candlesticks (cached) ----
def closing_mid(ticker, first_pitch):
    cf = os.path.join(CACHE, ticker + ".json")
    if os.path.exists(cf):
        cs = json.load(open(cf)).get("candlesticks", [])
    else:
        start = int((first_pitch - timedelta(days=4)).timestamp())
        end = int((first_pitch + timedelta(minutes=5)).timestamp())
        url = f"{BASE}/series/{SERIES}/markets/{ticker}/candlesticks?start_ts={start}&end_ts={end}&period_interval=60"
        d = get(url); json.dump(d, open(cf,"w")); cs = d.get("candlesticks", [])
        time.sleep(0.08)
    fp = first_pitch.timestamp()
    best = None
    for c in cs:
        if c.get("end_period_ts", 1e18) > fp: continue
        b = (c.get("yes_bid") or {}).get("close_dollars")
        a = (c.get("yes_ask") or {}).get("close_dollars")
        if b is not None and a is not None:
            best = (float(b) + float(a)) / 2
        elif (c.get("price") or {}).get("previous_dollars") is not None:
            best = float(c["price"]["previous_dollars"])
    return best

print("Pulling closing-line candlesticks ...")
for i, (ev, g) in enumerate(games.items()):
    for abbr, s in g["sides"].items():
        s["close_price"] = closing_mid(s["ticker"], g["first_pitch"])
    if (i+1) % 50 == 0: print(f"  {i+1}/{len(games)}")

# ---- 3. merge with model dataset ----
model = json.load(open("data/mlb-2026.06.18.json"))["games"]
model_by_key = {}
for mg in model:
    b = mg["bettingSummary"]
    key = (mg["date"][:10], frozenset([mg["homeTeam"], mg["awayTeam"]]))
    model_by_key[key] = (mg, b)

rows, unmatched = [], 0
for ev, g in games.items():
    teams = {ABBR2TEAM.get(a): s for a, s in g["sides"].items() if ABBR2TEAM.get(a)}
    if len(teams) != 2: continue
    key = (g["date"], frozenset(teams.keys()))
    if key not in model_by_key: unmatched += 1; continue
    mg, b = model_by_key[key]
    pick = b["predictedWinner"]
    opp = next(t for t in teams if t != pick) if pick in teams else None
    if opp is None: unmatched += 1; continue
    pick_side, opp_side = teams[pick], teams[opp]
    rows.append({
        "date": g["date"], "event": ev,
        "home": mg["homeTeam"], "away": mg["awayTeam"],
        "predictedWinner": pick, "actualWinner": b["actualWinner"],
        "winner_correct": int(pick == b["actualWinner"]),
        "predictedSpread": b["predictedSpread"], "casinoSpread": b["casinoSpread"],
        "casinoFavoredTeamName": b["casinoFavoredTeamName"],
        "predictedTotal": b["predictedTotal"], "casinoTotal": b["casinoTotal"],
        "kalshi_pick_close": pick_side.get("close_price"),
        "kalshi_opp_close": opp_side.get("close_price"),
        "kalshi_pick_result": pick_side.get("result"),  # 'yes' if pick won
    })

import csv
out = "data/kalshi_merged.csv"
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

priced = [r for r in rows if r["kalshi_pick_close"] is not None]
print(f"\nMerged games: {len(rows)}  (unmatched Kalshi events: {unmatched})")
print(f"Games with a Kalshi closing price: {len(priced)}")
print(f"Saved -> {out}")
