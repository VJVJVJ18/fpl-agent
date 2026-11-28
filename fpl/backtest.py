"""Walk-forward backtest of the xP model, parameter tuning, and the report.

Every prediction for gameweek g uses only matches before g (plus last season).
Periods:
  train    2025/26 GW6-24, plus 2026/27 GW6 to 4 gameweeks ago once there are 3+ such gameweeks
  valid    2025/26 GW25-38   never used for tuning
  holdout  the 4 most recent 2026/27 gameweeks   never used for tuning
New settings are kept only if neither valid nor holdout gets worse.

Usage:
  python -m fpl.backtest            # evaluate current params, write backtest/REPORT.md
  python -m fpl.backtest --tune     # tune on train, keep only if valid and test do not get worse
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import data, model, scoring

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "backtest" / "REPORT.md"
HORIZON = 6
PREV = {"2025-26": "2024-25", "2026-27": "2025-26"}
GRID = {
    "min_decay": [0.6, 0.75, 0.9],
    "rate_decay": [0.9, 0.97, 1.0],
    "k_rate": [3.0, 6.0, 12.0],
    "k_prior": [4.0, 8.0, 16.0],
    "k_team": [2.0, 4.0, 8.0],
    "home": [1.0, 1.1, 1.2],
    "att_power": [0.6, 1.0, 1.3],
    "k_dc": [3.0, 6.0, 12.0],
}


class Season:
    def __init__(self, season):
        self.season = season
        self.prev = data.load_past_season(PREV[season])
        if season == data.CURRENT_SEASON:
            self.df, self.boot, fx = data.load_current_season()
            self.fixtures = fx[["id", "event", "team_h", "team_a"]].dropna(subset=["event"]).astype({"event": int})
        else:
            self.df, self.boot = data.load_past_season(season), None
            self.fixtures = data.past_fixtures(season).dropna(subset=["event"]).astype({"event": int})
        self.last_gw = int(self.df["gw"].max())
        self.actual = self.df.groupby(["element", "gw"], as_index=False).agg(
            points=("points", "sum"), fpl_xp=("fpl_xp", "sum"), selected=("selected", "max"))


def predict(s, g, p, horizon=HORIZON):
    """Predictions for GW g..g+horizon-1 made with data before g, joined to what happened."""
    hist = s.df[s.df["gw"] < g]
    universe = hist.sort_values("gw").groupby("element").tail(1)
    gws = [x for x in range(g, g + horizon) if x <= s.last_gw]
    pr = model.project(hist, s.prev, s.fixtures, gws, universe, p)
    pr = pr.merge(s.actual, on=["element", "gw"], how="left")
    recent = hist[(hist["gw"] >= g - 4) & (hist["minutes"] > 0)]
    form = recent.groupby("element")["points"].sum() / 4
    pr["form"] = pr["element"].map(form).fillna(0) * pr["n_fix"]
    pr["pool"] = pr["element"].isin(recent["element"].unique())
    pr["origin"] = g
    return pr


def run(s, gws, p):
    return pd.concat([predict(s, g, p) for g in gws], ignore_index=True)


def metrics(res, col="xp"):
    """Accuracy on the decision pool (players who played in the last 4 gameweeks)."""
    one = res[(res["gw"] == res["origin"]) & res["pool"]].dropna(subset=["points", col])
    six = res[res["pool"]].groupby(["origin", "element"]).agg(
        pred=(col, "sum"), points=("points", "sum"), n=("gw", "count"))
    six = six[six["n"] == six["n"].max()].reset_index()
    rho1 = one.groupby("origin").apply(lambda d: spearmanr(d[col], d["points"]).statistic, include_groups=False)
    rho6 = six.groupby("origin").apply(lambda d: spearmanr(d["pred"], d["points"]).statistic, include_groups=False)
    top = one.sort_values(col, ascending=False).groupby("origin").head(20)
    return {
        "rho_1gw": rho1.mean(), "rho_6gw": rho6.mean(),
        "mae_1gw": (one[col] - one["points"]).abs().mean(),
        "bias_1gw": (one[col] - one["points"]).mean(),
        "top20_actual": top.groupby("origin")["points"].mean().mean(),
        "n_gw": one["origin"].nunique(),
    }


def score(m):
    """Single number used for tuning: ranking quality over 1 and 6 gameweeks."""
    return (m["rho_1gw"] + m["rho_6gw"]) / 2


def calibration(res):
    one = res[(res["gw"] == res["origin"]) & res["pool"]].dropna(subset=["points"])
    bins = pd.cut(one["xp"], [-np.inf, 2, 4, 6, np.inf], labels=["under 2", "2 to 4", "4 to 6", "6+"])
    return one.groupby(bins, observed=True).agg(n=("xp", "size"), predicted=("xp", "mean"), actual=("points", "mean"))


def differentials(res, per_pct, col="xp"):
    """Do the model's top picks deliver as well when they are low-owned?"""
    one = _pool_rows(res, per_pct)
    top = one.sort_values(col, ascending=False).groupby("origin").head(40)
    bands = pd.cut(top["own"], [-1, 5, 20, 101], labels=["under 5%", "5 to 20%", "over 20%"])
    return top.groupby(bands, observed=True).agg(n=(col, "size"), predicted=(col, "mean"),
                                                  actual=("points", "mean"))


def ownership_scale(s):
    """Managers per 1% ownership, from end-of-season counts and percentages."""
    raw = data._csv(s.season, "players_raw.csv")
    last = s.actual[s.actual["gw"] == s.last_gw].set_index("element")["selected"]
    sbp = raw.set_index("id")["selected_by_percent"]
    ratio = (last / sbp).replace([np.inf], np.nan).dropna()
    return float(ratio[sbp.reindex(ratio.index) > 1].median())


def _pool_rows(res, per_pct=None):
    one = res[(res["gw"] == res["origin"]) & res["pool"]].dropna(subset=["points"]).copy()
    if per_pct:
        one["own"] = (one["selected"] / per_pct).clip(0, 100)
    return one


def fit_calibration(res, per_pct):
    """Least squares on pool rows: points ~ a*P(play) + b*xP [+ c*ln(1+own)*P(play)]."""
    one = _pool_rows(res, per_pct)
    y = one["points"].to_numpy()
    X1 = np.column_stack([one["p_play"], one["xp"]])
    X2 = np.column_stack([one["p_play"], one["xp"], np.log1p(one["own"]) * one["p_play"]])
    c1, *_ = np.linalg.lstsq(X1, y, rcond=None)
    c2, *_ = np.linalg.lstsq(X2, y, rcond=None)
    return {"xp_only": [round(float(v), 4) for v in c1], "with_own": [round(float(v), 4) for v in c2]}


def _calibrated(res, per_pct, cal):
    """Pool rows with the live calibration (stats only, no ownership) in column xpc."""
    one = _pool_rows(res, per_pct)
    a, b = cal["xp_only"]
    one["xpc"] = (a * one["p_play"] + b * one["xp"]).clip(lower=0)
    return one


def fit_variance(res, per_pct, cal):
    """Spread of points around the calibrated projection: Var ~ a*xP + b*xP^2 (least squares
    on squared residuals). Used by the rank-aware objective (fpl/rank.py)."""
    one = _calibrated(res, per_pct, cal)
    X = np.column_stack([one["xpc"], one["xpc"] ** 2])
    coef, *_ = np.linalg.lstsq(X, (one["points"] - one["xpc"]) ** 2, rcond=None)
    return {"a": round(float(coef[0]), 4), "b": round(float(coef[1]), 4)}


def variance_check(res, per_pct, cal, vp):
    """Predicted against actual variance by projection band, on a period not used for the fit."""
    one = _calibrated(res, per_pct, cal)
    one["r2"] = (one["points"] - one["xpc"]) ** 2
    one["pred"] = vp["a"] * one["xpc"] + vp["b"] * one["xpc"] ** 2
    bins = pd.cut(one["xpc"], [-np.inf, 1, 2, 3, 4, 5, np.inf], labels=["under 1", "1-2", "2-3", "3-4", "4-5", "5+"])
    return one.groupby(bins, observed=True).agg(n=("r2", "size"), actual_var=("r2", "mean"), predicted_var=("pred", "mean"))


def captain_check(s, p):
    """Vuk's own squads this season: model captain within his XI against his actual captain."""
    rows = []
    for g in range(6, s.last_gw + 1):
        try:
            picks = data._json(data.COLLECTOR, f"picks/{g}.json")
        except Exception:
            continue
        xi = [x["element"] for x in picks["picks"] if x["multiplier"] > 0]
        cap = next(x["element"] for x in picks["picks"] if x["is_captain"])
        pr = predict(s, g, p, horizon=1)
        pr = pr[pr["element"].isin(xi)].set_index("element")
        if pr.empty:
            continue
        model_cap = pr["xp"].idxmax()
        pts = s.actual[s.actual["gw"] == g].set_index("element")["points"]
        rows.append({"gw": g, "model": int(pts.get(model_cap, 0)), "actual": int(pts.get(cap, 0)),
                     "same": model_cap == cap})
    return pd.DataFrame(rows)


def _train_score(trains, p):
    """Score over one or more (season, gameweeks) training sets, weighted by gameweeks."""
    total, n = 0.0, 0
    for s, gws in trains:
        m = metrics(run(s, gws, p))
        total += score(m) * m["n_gw"]
        n += m["n_gw"]
    return total / n


def tune(trains, p):
    """Coordinate search over GRID, two passes, maximising the training score."""
    best = dict(p)
    best_score = _train_score(trains, best)
    for _ in range(2):
        for key, values in GRID.items():
            for v in values:
                if v == best[key]:
                    continue
                trial = {**best, key: v}
                sc = _train_score(trains, trial)
                if sc > best_score + 1e-4:
                    best, best_score = trial, sc
    return best


def save_params(params, cal=None, variance=None):
    path = ROOT / "params.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "params": params, "calibration": cal or old.get("calibration"),
                                "variance": variance or old.get("variance")}, indent=2))


def main(tune_flag=False):
    s25, s26 = Season("2025-26"), Season(data.CURRENT_SEASON)
    L = s26.last_gw
    train = (s25, range(6, 25))
    trains = [train]
    periods = {"train (2025/26 GW6-24)": train}
    if L - 4 >= 8:  # this season joins the training data once it has 3+ gameweeks to spare
        trains.append((s26, range(6, L - 3)))
        periods[f"train (2026/27 GW6-{L - 4})"] = trains[-1]
    periods["valid (2025/26 GW25-38)"] = (s25, range(25, 39))
    periods[f"holdout (2026/27 GW{L - 3}-{L})"] = (s26, range(L - 3, L + 1))
    periods[f"season to date (2026/27 GW6-{L})"] = (s26, range(6, L + 1))
    current = model.load_params()
    results = {}
    if tune_flag:
        tuned = tune(trains, current)
        checks = [k for k in periods if k.startswith(("valid", "holdout"))]
        old = {k: metrics(run(*periods[k], current)) for k in checks}
        new = {k: metrics(run(*periods[k], tuned)) for k in checks}
        keep = all(score(new[k]) >= score(old[k]) - 0.005 for k in new)
        params = tuned if keep else current
        results["tuning"] = {"kept_new": keep, "old": {k: score(v) for k, v in old.items()},
                             "new": {k: score(v) for k, v in new.items()}}
        if keep:
            save_params(params)
    else:
        params = current

    lines = [f"# Backtest report", "",
             f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Every prediction uses only data "
             "from before the gameweek it predicts. Accuracy is measured on the decision pool: players who "
             "played in the previous 4 gameweeks.", ""]
    lines += ["## Accuracy by period", "",
              "| Period | Rank corr. 1 GW | Rank corr. 6 GW | MAE 1 GW | Bias | Top-20 avg pts | GWs |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    all_res = {}
    for name, (s, gws) in periods.items():
        res = run(s, gws, params)
        all_res[name] = res
        m = metrics(res)
        lines.append(f"| {name} | {m['rho_1gw']:.3f} | {m['rho_6gw']:.3f} | {m['mae_1gw']:.2f} | "
                     f"{m['bias_1gw']:+.2f} | {m['top20_actual']:.2f} | {m['n_gw']} |")
    lines += ["", "## Against baselines (2025/26 GW6-38, one gameweek ahead)", "",
              "| Method | Rank corr. | MAE | Top-20 avg pts |", "| --- | --- | --- | --- |"]
    both = pd.concat([all_res[k] for k in all_res if "2025/26" in k])
    for label, col in [("This model", "xp"), ("Form (avg points, last 4 GWs)", "form")]:
        m = metrics(both, col)
        lines.append(f"| {label} | {m['rho_1gw']:.3f} | {m['mae_1gw']:.2f} | {m['top20_actual']:.2f} |")
    lines += ["", "FPL's own archived xP is not used as a baseline: in the archive it is 0 for 83% of players "
              "who then played 90 minutes, so it is not the pre-deadline projection."]
    lines += ["", "## Calibration (2025/26 GW6-38)", "", calibration(both).round(2).to_markdown(), ""]
    per_pct = ownership_scale(s25)
    cal = fit_calibration(all_res["train (2025/26 GW6-24)"], per_pct)
    valid = all_res["valid (2025/26 GW25-38)"]
    one = _pool_rows(valid, per_pct)
    calibrated = model.calibrate(valid.assign(xp=valid["xp"]), own=None, cal=cal)
    with_own = valid.copy()
    with_own["own"] = (with_own["selected"] / per_pct).clip(0, 100)
    a, b, c = cal["with_own"]
    with_own["xp"] = a * with_own["p_play"] + b * with_own["xp"] + c * np.log1p(with_own["own"]) * with_own["p_play"]
    lines += [f"## Calibration fitted on train, checked on valid", "",
              f"Fitted coefficients: {json.dumps(cal)}.", "",
              "| Version (valid period) | MAE 1 GW | Rank corr. 1 GW |", "| --- | --- | --- |"]
    for label, r in [("Raw", valid), ("Calibrated (live)", calibrated),
                     ("Calibrated + ownership (research only, not used live)", with_own)]:
        m = metrics(r)
        lines.append(f"| {label} | {m['mae_1gw']:.3f} | {m['rho_1gw']:.3f} |")
    lines += ["", "Calibration by projected points, valid period, calibrated (live):", "",
              calibration(calibrated).round(2).to_markdown(), ""]
    lines += ["## Do low-owned picks deliver? (model's top 40 each gameweek, valid period)", "",
              f"Ownership from FPL's counts ({per_pct * 100 / 1e6:.1f} million managers). If low-owned picks score "
              "close to their projection, the model's differentials can be trusted.", "",
              "Raw model:", "", differentials(valid, per_pct).round(2).to_markdown(), "",
              "Calibrated (live):", "", differentials(calibrated.assign(selected=valid["selected"]), per_pct).round(2).to_markdown(), ""]
    cap = captain_check(s26, params)
    if len(cap):
        lines += ["## Captaincy on Vuk's own XI (2026/27)", "",
                  f"Model's captain scored {cap['model'].sum()} points against {cap['actual'].sum()} for the "
                  f"captain actually picked, over GW{cap['gw'].min()}-{cap['gw'].max()} "
                  f"(same choice in {int(cap['same'].sum())} of {len(cap)} gameweeks).", "",
                  cap.to_markdown(index=False), ""]
    if "tuning" in results:
        t = results["tuning"]
        lines += ["## Tuning", "", f"New parameters kept: {t['kept_new']}.", "",
                  "| Period | Score before | Score after |", "| --- | --- | --- |"]
        lines += [f"| {k} | {t['old'][k]:.3f} | {t['new'][k]:.3f} |" for k in t["old"]]
        lines.append("")
    vp = fit_variance(all_res["train (2025/26 GW6-24)"], per_pct, cal)
    lines += ["## Spread of points around the projection (for the rank-aware objective)", "",
              f"Fitted on train: Var(points) = {vp['a']} x xP + {vp['b']} x xP^2. Checked on valid:", "",
              variance_check(valid, per_pct, cal, vp).round(2).to_markdown(), ""]
    save_params(params, cal, vp)
    lines += ["## Parameters", "", "```json", json.dumps(params, indent=2), "```", ""]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main(tune_flag="--tune" in sys.argv)
