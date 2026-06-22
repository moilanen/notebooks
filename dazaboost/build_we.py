"""
Build an empirical MLB win-expectancy table from play-by-play across all completed 2026 games.
WE(inning, half, lead) = P(team that is currently ahead/behind by `lead` runs eventually wins),
sampled at the end of each half-inning. Output: data/win_expectancy.csv
"""
import json, os, time, csv, urllib.request, urllib.error
from datetime import date, timedelta

SCHED = "data/sched_cache"; PBP = "data/pbp_cache"
os.makedirs(SCHED, exist_ok=True); os.makedirs(PBP, exist_ok=True)
START, END = date(2026, 3, 18), date(2026, 6, 20)

def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept":"application/json"}), timeout=40) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries-1: raise
            time.sleep(1.0*(i+1))

def schedule(d):
    cf = os.path.join(SCHED, d.isoformat() + ".json")
    if os.path.exists(cf): return json.load(open(cf))
    j = get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d.isoformat()}")
    json.dump(j, open(cf, "w")); time.sleep(0.04); return j

def pbp_plays(gpk):
    cf = os.path.join(PBP, f"{gpk}.json")
    if os.path.exists(cf):
        return json.load(open(cf))
    d = get(f"https://statsapi.mlb.com/api/v1.1/game/{gpk}/feed/live")
    plays = [{"endTime": p["about"].get("endTime"), "inning": p["about"]["inning"],
              "isTop": p["about"]["isTopInning"], "a": p["result"].get("awayScore"),
              "h": p["result"].get("homeScore")} for p in d["liveData"]["plays"]["allPlays"]]
    json.dump(plays, open(cf, "w")); time.sleep(0.04)
    return plays

# ---- collect completed games ----
print("Collecting completed games ...")
games = []
d = START
while d <= END:
    try:
        j = schedule(d)
    except Exception as e:
        print(f"  ! schedule {d}: {e}"); d += timedelta(days=1); continue
    for dt in j.get("dates", []):
        for g in dt["games"]:
            st = g.get("status", {}).get("detailedState", "")
            if st in ("Final", "Game Over", "Completed Early"):
                home_won = g["teams"]["home"].get("isWinner")
                if home_won is None: continue
                games.append((g["gamePk"], int(bool(home_won))))
    d += timedelta(days=1)
print(f"  {len(games)} completed games")

# ---- sample state at end of each half-inning ----
from collections import defaultdict
agg = defaultdict(lambda: [0, 0])   # (inning, half, home_lead) -> [home_wins, n]
done = 0
for gpk, home_won in games:
    try:
        plays = pbp_plays(gpk)
    except Exception:
        continue
    # last play of each (inning, isTop)
    last = {}
    for p in plays:
        if p["a"] is None or p["h"] is None: continue
        last[(p["inning"], p["isTop"])] = (p["a"], p["h"])
    for (inning, is_top), (a, h) in last.items():
        inn_key = inning if inning <= 9 else 10        # 10 = extras
        lead = h - a
        # clamp lead for table sanity
        lead = max(-9, min(9, lead))
        cell = agg[(inn_key, "T" if is_top else "B", lead)]
        cell[0] += home_won; cell[1] += 1
    done += 1
    if done % 150 == 0: print(f"  processed {done}/{len(games)}")

rows = []
for (inn, half, lead), (w, n) in sorted(agg.items()):
    rows.append({"inning": inn, "half": half, "home_lead": lead,
                 "n": n, "home_win_rate": round(w/n, 4)})
with open("data/win_expectancy.csv", "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=["inning","half","home_lead","n","home_win_rate"]); wr.writeheader(); wr.writerows(rows)
print(f"\nWin-expectancy cells: {len(rows)} (from {done} games) -> data/win_expectancy.csv")
