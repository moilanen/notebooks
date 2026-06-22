"""
For each model-dog game: find the moment its Kalshi price bottomed, then reconstruct the live MLB
game state at that moment (inning, dog deficit, innings left) + the model's expected remaining dog
runs. Output data/comeback_state.csv for surface analysis.
"""
import json, os, time, csv, glob, urllib.request, urllib.error
from datetime import datetime, timezone
import pandas as pd

CACHE = "data/comeback_cache"; PBP = "data/pbp_cache"; os.makedirs(PBP, exist_ok=True)
SCHED = "data/sched_cache"; os.makedirs(SCHED, exist_ok=True)
TEAM2ABBR = {"Oakland Athletics":"ATH","Atlanta Braves":"ATL","Arizona Diamondbacks":"AZ","Baltimore Orioles":"BAL","Boston Red Sox":"BOS","Chicago Cubs":"CHC","Cincinnati Reds":"CIN","Cleveland Guardians":"CLE","Colorado Rockies":"COL","Chicago White Sox":"CWS","Detroit Tigers":"DET","Houston Astros":"HOU","Kansas City Royals":"KC","Los Angeles Angels":"LAA","Los Angeles Dodgers":"LAD","Miami Marlins":"MIA","Milwaukee Brewers":"MIL","Minnesota Twins":"MIN","New York Mets":"NYM","New York Yankees":"NYY","Philadelphia Phillies":"PHI","Pittsburgh Pirates":"PIT","San Diego Padres":"SD","Seattle Mariners":"SEA","San Francisco Giants":"SF","St. Louis Cardinals":"STL","Tampa Bay Rays":"TB","Texas Rangers":"TEX","Toronto Blue Jays":"TOR","Washington Nationals":"WSH"}
MON = {4:"APR",5:"MAY",6:"JUN"}

def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept":"application/json"}), timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries-1: raise
            time.sleep(1.0*(i+1))

def dip_moment(ticker):
    """Return (min_price, unix_ts_of_min) from cached 1-min candles with volume."""
    cf = os.path.join(CACHE, ticker + ".json")
    if not os.path.exists(cf): return None, None
    cs = json.load(open(cf)).get("candlesticks", [])
    lo, lo_ts = None, None
    for c in cs:
        try: v = float(c.get("volume_fp", 0) or 0)
        except: v = 0
        pr = (c.get("price") or {}).get("low_dollars")
        if v > 0 and pr is not None:
            fv = float(pr)
            if lo is None or fv < lo: lo, lo_ts = fv, c.get("end_period_ts")
    return lo, lo_ts

def schedule_for(datestr):
    cf = os.path.join(SCHED, datestr + ".json")
    if os.path.exists(cf): return json.load(open(cf))
    d = get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={datestr}")
    json.dump(d, open(cf, "w")); time.sleep(0.05); return d

def gamepk_for(datestr, home, away):
    d = schedule_for(datestr)
    for dt in d.get("dates", []):
        for g in dt["games"]:
            if g["teams"]["home"]["team"]["name"] == home and g["teams"]["away"]["team"]["name"] == away:
                return g["gamePk"]
    return None

def pbp_state_at(gamepk, ts_unix):
    """State at the last play whose endTime <= ts: (inning, isTop, awayScore, homeScore, final_away, final_home)."""
    cf = os.path.join(PBP, f"{gamepk}.json")
    if os.path.exists(cf):
        plays = json.load(open(cf))
    else:
        d = get(f"https://statsapi.mlb.com/api/v1.1/game/{gamepk}/feed/live")
        plays = [{"endTime": p["about"].get("endTime"), "inning": p["about"]["inning"],
                  "isTop": p["about"]["isTopInning"], "a": p["result"].get("awayScore"),
                  "h": p["result"].get("homeScore")} for p in d["liveData"]["plays"]["allPlays"]]
        json.dump(plays, open(cf, "w")); time.sleep(0.05)
    if not plays: return None
    final_a = next((p["a"] for p in reversed(plays) if p["a"] is not None), None)
    final_h = next((p["h"] for p in reversed(plays) if p["h"] is not None), None)
    state = None
    for p in plays:
        et = p.get("endTime")
        if not et: continue
        pts = datetime.fromisoformat(et.replace("Z", "+00:00")).timestamp()
        if pts <= ts_unix:
            state = p
        else:
            break
    if state is None: state = plays[0]
    return state["inning"], state["isTop"], state["a"] or 0, state["h"] or 0, final_a, final_h

# ---- run over model-dog games ----
df = pd.read_csv("data/kalshi_merged.csv")
df = df[df.predictedWinner != df.casinoFavoredTeamName].copy()
res = {r["ticker"]: r["result"] for r in csv.DictReader(open("data/comeback.csv"))}
ticker_by = {}
for t in res:                     # index tickers for matching
    ticker_by.setdefault(t.split("-")[1][:7], {})[t.rsplit("-",1)[-1]] = t

rows = []
for i, (_, r) in enumerate(df.iterrows()):
    dog = r.predictedWinner; abbr = TEAM2ABBR.get(dog)
    gd = r.date.replace("-", ""); dc = f"{gd[2:4]}{MON.get(int(gd[4:6]),'')}{gd[6:8]}"
    tk = (ticker_by.get(dc) or {}).get(abbr)
    if not tk: continue
    lo, lo_ts = dip_moment(tk)
    if lo is None or lo_ts is None: continue
    gpk = gamepk_for(r.date, r.home, r.away)
    if not gpk: continue
    st = pbp_state_at(gpk, lo_ts)
    if st is None: continue
    inning, is_top, a_now, h_now, fa, fh = st
    dog_is_home = (dog == r.home)
    dog_now = h_now if dog_is_home else a_now
    opp_now = a_now if dog_is_home else h_now
    deficit = opp_now - dog_now                      # runs behind at the dip (>0 = behind)
    # innings the dog still gets to bat (approx): full innings remaining + current if not yet batted
    innings_left = max(0.0, 9 - inning)
    if (dog_is_home and is_top) or ((not dog_is_home) and (not is_top) and inning <= 9):
        innings_left += 1                            # dog still bats this inning
    # model's predicted final runs for the dog -> expected remaining
    home_pred = (r.predictedTotal + r.predictedSpread) / 2.0
    away_pred = (r.predictedTotal - r.predictedSpread) / 2.0
    dog_pred_final = home_pred if dog_is_home else away_pred
    exp_remaining_dog = dog_pred_final * (innings_left / 9.0)
    won = 1 if res.get(tk) == "yes" else 0
    rows.append({"date": r.date, "dog": dog, "dip_price": lo, "inning": inning,
                 "innings_left": round(innings_left, 1), "deficit": deficit,
                 "dog_pred_final": round(dog_pred_final, 1),
                 "exp_remaining_dog": round(exp_remaining_dog, 2),
                 "run_surplus": round(exp_remaining_dog - deficit, 2), "won": won})
    if (i+1) % 25 == 0: print(f"  processed {i+1}")

with open("data/comeback_state.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(f"\nReconstructed live state for {len(rows)} model-dog games -> data/comeback_state.csv")
