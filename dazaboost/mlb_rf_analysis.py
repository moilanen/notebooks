"""
MLB prediction-model evaluation with Random Forest.

Goal: the source JSON was produced by some predictive model that calls
winner / spread / total for each game. We:
  1. Measure that model's RAW accuracy & precision on each task (vs actuals).
  2. Train a RandomForest *meta-classifier* to predict, from pre-game
     features only, whether each call will be correct -- and read its
     feature importances to see WHAT the model is good at / when to trust it.
"""

import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score, precision_score, confusion_matrix

DATA = "data/mlb-2026.06.18.json"

# ---------------------------------------------------------------- load
games = json.load(open(DATA))["games"]
rows = []
for g in games:
    b = g.get("bettingSummary", {})
    r = dict(b)
    r["homeTeam"] = g["homeTeam"]
    r["awayTeam"] = g["awayTeam"]
    r["date"] = g["date"][:10]
    rows.append(r)
df = pd.DataFrame(rows)
df = df[df["hasResult"] == True].copy()
print(f"Games with results: {len(df)}  ({df.date.min()} -> {df.date.max()})")

# ---------------------------------------------------------------- targets (correctness of the source model)
df["winner_correct"] = (df["predictedWinner"] == df["actualWinner"]).astype(int)
# spread graded vs the casino line: did the model's predicted margin land on the
# same side of the -1.5/+1.5 number as the actual result? (home-team reference)
_home_covers_actual = (df["actualSpread"] + df["casinoSpread"]) > 0
_home_covers_pred   = (df["predictedSpread"] + df["casinoSpread"]) > 0
df["spread_correct"] = (_home_covers_actual == _home_covers_pred).astype(int)
df["total_correct"]  = (df["predictedTotalVerdict"] == df["actualTotalVerdict"])

# ---------------------------------------------------------------- pre-game features (NO actual* leakage)
df["spread_vs_casino"] = df["predictedSpread"] - df["casinoSpread"]
df["total_vs_casino"]  = df["predictedTotal"]  - df["casinoTotal"]
df["agrees_with_casino_fav"] = (df["predictedFavoredTeamName"] == df["casinoFavoredTeamName"]).astype(int)
df["abs_pred_spread"]  = df["predictedSpread"].abs()
df["score_adj_total"]  = df["homeScoreAdjustment"].abs() + df["awayScoreAdjustment"].abs()

FEATURES = [
    "predictedSpread", "predictedTotal", "casinoSpread", "casinoTotal",
    "spread_vs_casino", "total_vs_casino", "agrees_with_casino_fav",
    "abs_pred_spread", "baselinePredictedHomeScore", "baselinePredictedAwayScore",
    "baselinePredictedSpread", "baselinePredictedTotal",
    "adjustedPredictedSpread", "adjustedPredictedTotal",
    "homeScoreAdjustment", "awayScoreAdjustment", "score_adj_total",
    "hasRosterAnalysis",
]


def evaluate(target_col, label):
    print("\n" + "=" * 70)
    print(f"TASK: {label}")
    print("=" * 70)
    sub = df.dropna(subset=[target_col]).copy()
    sub = sub.dropna(subset=FEATURES)
    X = sub[FEATURES].astype(float)
    y = sub[target_col].astype(int)

    # (1) raw model performance vs actual
    base_acc = y.mean()
    print(f"  n = {len(y)}")
    print(f"  Source-model RAW accuracy on this task : {base_acc:6.3f}  "
          f"({y.sum()} correct / {len(y)})")

    # (2) RF meta-classifier: can we predict when the call is right?
    rf = RandomForestClassifier(n_estimators=400, min_samples_leaf=3,
                                class_weight="balanced", random_state=42, n_jobs=-1)
    cv = StratifiedKFold(5, shuffle=True, random_state=42)
    pred = cross_val_predict(rf, X, y, cv=cv)
    acc = accuracy_score(y, pred)
    prec = precision_score(y, pred, zero_division=0)
    print(f"  RF meta-model  accuracy (5-fold CV)    : {acc:6.3f}")
    print(f"  RF meta-model  precision (call=correct): {prec:6.3f}")
    print(f"  confusion matrix [rows=actual, cols=pred] (0=wrong,1=correct):")
    print("   ", confusion_matrix(y, pred).tolist())

    # feature importances on full fit
    rf.fit(X, y)
    imp = pd.Series(rf.feature_importances_, index=FEATURES).sort_values(ascending=False)
    print("  Top features driving when this call is trustworthy:")
    for f, v in imp.head(6).items():
        print(f"     {f:32s} {v:6.3f}")
    return dict(task=label, n=len(y), raw_acc=base_acc, rf_acc=acc, rf_prec=prec)


results = [
    evaluate("winner_correct", "Winner pick"),
    evaluate("spread_correct", "Spread cover"),
    evaluate("total_correct",  "Total Over/Under"),
]

print("\n" + "#" * 70)
print("SUMMARY")
print("#" * 70)
print(pd.DataFrame(results).to_string(index=False))
