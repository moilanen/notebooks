"""
Scan settled MLB markets for the 'left for dead' comeback: a team's contract trades down to
<=X cents intraday, then the team wins. Uses 1-minute candlesticks over the game window and the
minimum TRADED price (price.low in candles with volume) to avoid empty-book false zeros.
Output: data/comeback.csv (one row per contract-side) + printed summary.
"""
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXMLBGAME"
CACHE = "data/comeback_cache"
os.makedirs(CACHE, exist_ok=True)
MON = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept":"application/json"}), timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if i == tries-1: raise
            time.sleep(1.2*(i+1))

def first_pitch_utc(ticker):
    body = ticker[len(SERIES)+1:]
    code, _ = body.rsplit("-", 1)
    yy, mon, dd = int(code[:2]), MON[code[2:5]], int(code[5:7])
    hh, mm = int(code[7:9]), int(code[9:11])
    et = datetime(2000+yy, mon, dd, hh, mm, tzinfo=timezone(timedelta(hours=-4)))
    return et.astimezone(timezone.utc)

# ---- collect settled markets ----
print("Fetching settled MLB markets ...")
markets, cursor, pages = [], None, 0
while pages < 12:
    url = f"{BASE}/markets?series_ticker={SERIES}&status=settled&limit=1000"
    if cursor: url += f"&cursor={cursor}"
    d = get(url); pages += 1
    markets += d.get("markets", [])
    cursor = d.get("cursor")
    if not cursor: break
markets = [m for m in markets if m.get("result") in ("yes", "no")]
print(f"  {len(markets)} settled contract-sides")

def min_traded_price(ticker):
    """Lowest last-trade price during the game window, from 1-min candles with volume."""
    cf = os.path.join(CACHE, ticker + ".json")
    if os.path.exists(cf):
        cs = json.load(open(cf)).get("candlesticks", [])
    else:
        try:
            fp = first_pitch_utc(ticker)
        except Exception:
            return None
        start = int(fp.timestamp()); end = start + int(6*3600)
        url = f"{BASE}/series/{SERIES}/markets/{ticker}/candlesticks?start_ts={start}&end_ts={end}&period_interval=1"
        try:
            d = get(url); json.dump(d, open(cf, "w")); cs = d.get("candlesticks", [])
        except Exception:
            json.dump({"candlesticks": []}, open(cf, "w")); cs = []
        time.sleep(0.05)
    lo = None
    for c in cs:
        vol = c.get("volume", 0) or 0
        pr = c.get("price") or {}
        v = pr.get("low_dollars")
        if vol > 0 and v is not None:
            fv = float(v)
            lo = fv if lo is None else min(lo, fv)
    return lo

print("Scanning intraday lows (1-min candles) ...")
rows = []
for i, m in enumerate(markets):
    lo = min_traded_price(m["ticker"])
    rows.append({"ticker": m["ticker"], "result": m["result"], "min_traded_price": lo})
    if (i+1) % 200 == 0: print(f"  {i+1}/{len(markets)}")

import csv
with open("data/comeback.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["ticker","result","min_traded_price"]); w.writeheader(); w.writerows(rows)

priced = [r for r in rows if r["min_traded_price"] is not None]
print(f"\nContract-sides with intraday trade data: {len(priced)} / {len(rows)}")
print(f"{'dipped to <=':14s}{'n_dipped':>10s}{'won_after':>11s}{'comeback_rate':>15s}")
for thr in (0.01, 0.02, 0.03, 0.05, 0.10):
    dipped = [r for r in priced if r["min_traded_price"] <= thr]
    won = sum(1 for r in dipped if r["result"] == "yes")
    rate = (won/len(dipped)) if dipped else float("nan")
    print(f"{f'{int(thr*100)}c':14s}{len(dipped):>10d}{won:>11d}{rate:>14.1%}" if dipped else f"{f'{int(thr*100)}c':14s}{0:>10d}")
print("\nSaved -> data/comeback.csv")
