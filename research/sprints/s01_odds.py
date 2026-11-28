"""Sprint 1 (28 Nov 2026): do bookmaker odds beat the model, and do they improve player rankings?

  python -m research.sprints.s01_odds      # needs research/out/panel.parquet (python -m research.run)

1. Team level: Poisson log-likelihood and clean-sheet Brier score of goals conceded, model vs market.
2. Player level, walk-forward: v1 overlay vs overlay + odds features, and a defender-only swap of
   the model's goals-against rate for the market's.
Result and verdict: research/LOG.md, sprint 1.
"""
import numpy as np
import pandas as pd
from scipy.stats import poisson, spearmanr

from fpl import data
from research import combiner, odds
from research.panel import OUT

SEASONS = ["2023-24", "2024-25", "2025-26", data.CURRENT_SEASON]
CS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
GC = {"GK": -1, "DEF": -1, "MID": 0, "FWD": 0}


def team_level(raw, rates):
    one = raw[(raw["h"] == 0) & raw["cs_prob"].notna()]
    fx = one.groupby(["season", "gw", "opp", "home"], as_index=False)["cs_prob"].median()
    fx["lam_model"] = -np.log(fx["cs_prob"].clip(1e-6, 1))
    rows = []
    for s, r in rates.items():
        res = data._csv(s, "fixtures.csv")
        res = res[res["finished"].astype(str).str.lower().isin(["true", "1"])]
        act = pd.concat([pd.DataFrame({"gw": res["event"], "opp": res["team_a"], "home": 1.0, "k": res["team_a_score"]}),
                         pd.DataFrame({"gw": res["event"], "opp": res["team_h"], "home": 0.0, "k": res["team_h_score"]})])
        d = odds.team_side(r).merge(act.dropna(), on=["gw", "opp", "home"]).merge(fx[fx["season"] == s],
                                                                                  on=["gw", "opp", "home"])
        k = d["k"].astype(int)
        for name, lam in (("model", d["lam_model"]), ("market", d["o_lam_against"])):
            rows.append({"season": s, "forecaster": name, "matches": len(d), "loglik": poisson.logpmf(k, lam).mean(),
                         "cs_brier": ((np.exp(-lam) - (k == 0)) ** 2).mean()})
    return pd.DataFrame(rows)


def defender_swap(P):
    """The model's xP with the market's goals-against rate for the next gameweek's clean sheet terms."""
    m = (P["h"] == 0) & P["o_lam_against"].notna()
    lam = P["o_lam_against"]
    cs = P["pos"].map(CS) * P["p60"] * np.exp(-lam)
    gc = P["pos"].map(GC) * (lam / 2 - (1 - np.exp(-2 * lam)) / 4) * (P["e_min"] / 90)
    out = P["xp"].copy()
    out[m] = (P["xp"] - P["x_cs"] - P["x_gc"] + cs + gc)[m]
    return out


def main():
    rates = {s: odds.season_fixture_rates(s) for s in SEASONS}
    raw = odds.add_features(pd.read_parquet(OUT), rates)
    print(team_level(raw, rates).round(4).to_string(index=False))
    P = combiner.prepare(raw[raw["pool"] & raw["points"].notna()].copy())
    P["xp_def_swap"] = defender_swap(P)
    combiner.FEATURES["overlay_odds"] = combiner.FEATURES["overlay"] + odds.FEATURES
    combiner.RESIDUAL.add("overlay_odds")
    res = combiner.walk_forward(P, ["2024-25", "2025-26", data.CURRENT_SEASON], ["overlay", "overlay_odds"])
    res["pred_def_swap"] = res["xp_def_swap"]
    for name, mask in combiner.periods(res).items():
        r = res[mask]
        print("\n" + name)
        print(pd.DataFrame({k: combiner.paired(r, "pred_" + a, "pred_" + b) for k, a, b in
                            [("overlay+odds vs v1", "overlay", "overlay_odds"),
                             ("defender swap vs live", "base", "def_swap")]}).T.round(3).to_string())
        d = r[(r["h"] == 0) & (r["pos"] == "DEF")]
        ic = d.groupby(["season", "origin"]).apply(
            lambda x: spearmanr(x["pred_def_swap"], x["points"]).statistic - spearmanr(x["xp"], x["points"]).statistic)
        print(f"defender rank IC, swap minus live: {ic.mean():+.3f} (t {ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic)):.2f})")


if __name__ == "__main__":
    main()
