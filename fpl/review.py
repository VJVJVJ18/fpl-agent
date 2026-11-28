"""Post-gameweek review: how good were this gameweek's projections, and retune if warranted.

  python -m fpl.review 12

1. Compares the projections logged at brief time (predictions/<season>/gw12.csv)
   with what happened: rank correlation, error, bias, the model's top 20, and
   Vuk's own XI and captain.
2. Appends one row to reviews/<season>.csv (the season-long track record).
3. Every 3rd review, re-tunes the model on all data up to 4 gameweeks ago and
   keeps the new settings only if they do at least as well on the 4 most recent
   gameweeks and on last season's held-out period (python -m fpl.backtest --tune).
Prints a short summary for the next brief.
"""
import csv
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

from . import backtest, data

ROOT = Path(__file__).resolve().parent.parent
SEASON = data.CURRENT_SEASON
FIELDS = ["gw", "players", "rank_corr", "mae", "bias", "top20_actual", "xi_predicted", "xi_actual",
          "captain", "captain_predicted", "captain_actual", "best_captain_in_xi", "best_captain_actual", "retuned"]


def review(gw):
    pred_path = ROOT / "predictions" / SEASON / f"gw{gw}.csv"
    if not pred_path.exists():
        raise SystemExit(f"No logged predictions for GW{gw}: nothing to review.")
    pred = pd.read_csv(pred_path)
    live = data._json(data.COLLECTOR, f"live/{gw}.json")
    actual = pd.Series({x["id"]: x["stats"]["total_points"] for x in live["elements"]}, name="points")
    names = {e["id"]: e["web_name"] for e in data._json(data.COLLECTOR, "bootstrap.json")["elements"]}
    df = pred.join(actual, on="element").dropna(subset=["points"])
    pool = df[df["p_play"] >= 0.3]
    row = {"gw": gw, "players": len(pool),
           "rank_corr": round(spearmanr(pool["xp"], pool["points"]).statistic, 3),
           "mae": round((pool["xp"] - pool["points"]).abs().mean(), 2),
           "bias": round((pool["xp"] - pool["points"]).mean(), 2),
           "top20_actual": round(pool.nlargest(20, "xp")["points"].mean(), 2)}
    try:
        picks = data._json(data.COLLECTOR, f"picks/{gw}.json")["picks"]
        xi = [p["element"] for p in picks if p["multiplier"] > 0]
        cap = next(p["element"] for p in picks if p["is_captain"])
        mine = df[df["element"].isin(xi)].set_index("element")
        best = mine["xp"].idxmax()
        row.update({"xi_predicted": round(mine["xp"].sum(), 1), "xi_actual": int(mine["points"].sum()),
                    "captain": names.get(cap), "captain_predicted": round(mine["xp"].get(cap, 0), 2),
                    "captain_actual": int(mine["points"].get(cap, 0)),
                    "best_captain_in_xi": names.get(best), "best_captain_actual": int(mine["points"].get(best, 0))})
    except Exception:
        pass

    path = ROOT / "reviews" / f"{SEASON}.csv"
    path.parent.mkdir(exist_ok=True)
    history = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=FIELDS)
    retune = (len(history) + 1) % 3 == 0
    row["retuned"] = ""
    if retune:
        before = backtest.model.load_params()
        backtest.main(tune_flag=True)
        row["retuned"] = "changed" if backtest.model.load_params() != before else "unchanged"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})
    shadow_line = review_shadow(gw, actual)
    history = pd.read_csv(path)
    print(f"GW{gw} review: rank corr {row['rank_corr']} (season avg {history['rank_corr'].mean():.3f}), "
          f"MAE {row['mae']}, bias {row['bias']:+}, model top-20 avg {row['top20_actual']} pts.")
    if "xi_actual" in row:
        print(f"Your XI: projected {row['xi_predicted']}, scored {row['xi_actual']}. Captain {row['captain']} "
              f"scored {row['captain_actual']}; model's pick {row['best_captain_in_xi']} scored {row['best_captain_actual']}.")
    if retune:
        print(f"Model retune: {row['retuned']} (see backtest/REPORT.md).")
    if shadow_line:
        print(shadow_line)
    return row


SHADOW_FIELDS = ["gw", "players", "rank_live", "rank_shadow", "rank_shadow_flagged",
                 "top20_live", "top20_shadow", "top20_shadow_flagged"]
GATE_MIN_GWS, GATE_WINS, GATE_MEAN = 6, 5, 0.02


def shadow_gate(hist, col="rank_shadow_flagged"):
    """Live gate for the shadow model (see fpl/shadow.py). Returns (passed, summary)."""
    d = hist[col] - hist["rank_live"]
    t20 = (hist[col.replace("rank", "top20")] - hist["top20_live"]).mean()
    wins, n = int((d > 0).sum()), len(d)
    passed = n >= GATE_MIN_GWS and wins >= GATE_WINS * n / GATE_MIN_GWS and d.mean() >= GATE_MEAN and t20 >= 0
    return passed, f"ahead in {wins} of {n} GWs, rank corr {d.mean():+.3f} on average, top-20 {t20:+.2f} pts"


def review_shadow(gw, actual):
    """Score the shadow model's logged projections for a gameweek against the live model's."""
    path = ROOT / "predictions" / SEASON / f"gw{gw}_shadow.csv"
    if not path.exists():
        return None
    sh = pd.read_csv(path).join(actual, on="element").dropna(subset=["points"])
    pool = sh[sh["p_play"] >= 0.3]
    row = {"gw": gw, "players": len(pool)}
    for name, col in [("live", "xp_live"), ("shadow", "xp_shadow"), ("shadow_flagged", "xp_shadow_flagged")]:
        row[f"rank_{name}"] = round(spearmanr(pool[col], pool["points"]).statistic, 3)
        row[f"top20_{name}"] = round(pool.nlargest(20, col)["points"].mean(), 2)
    out = ROOT / "reviews" / f"{SEASON}_shadow.csv"
    hist = pd.read_csv(out) if out.exists() else pd.DataFrame(columns=SHADOW_FIELDS)
    hist = pd.concat([hist[hist["gw"] != gw], pd.DataFrame([row])], ignore_index=True).sort_values("gw")
    hist[SHADOW_FIELDS].to_csv(out, index=False)
    passed, summary = shadow_gate(hist)
    line = (f"Shadow model GW{gw}: rank corr {row['rank_shadow_flagged']} vs live {row['rank_live']}; "
            f"season so far {summary}.")
    if passed:
        line += " It passes the live gate: propose promotion to Vuk with these numbers (BRIEF.md section 7)."
    return line


if __name__ == "__main__":
    review(int(sys.argv[1]))
