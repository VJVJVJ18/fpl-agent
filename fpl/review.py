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
    history = pd.read_csv(path)
    print(f"GW{gw} review: rank corr {row['rank_corr']} (season avg {history['rank_corr'].mean():.3f}), "
          f"MAE {row['mae']}, bias {row['bias']:+}, model top-20 avg {row['top20_actual']} pts.")
    if "xi_actual" in row:
        print(f"Your XI: projected {row['xi_predicted']}, scored {row['xi_actual']}. Captain {row['captain']} "
              f"scored {row['captain_actual']}; model's pick {row['best_captain_in_xi']} scored {row['best_captain_actual']}.")
    if retune:
        print(f"Model retune: {row['retuned']} (see backtest/REPORT.md).")
    return row


if __name__ == "__main__":
    review(int(sys.argv[1]))
