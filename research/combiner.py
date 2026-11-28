"""Walk-forward forecast models: can a learned model beat the agent's structural xP out of sample?

For each test origin g, a model is trained only on rows whose target gameweek finished before g
(earlier seasons in full, plus this season's finished gameweeks), then predicts GW g..g+5.
Retrained every RETRAIN origins.

Variants (feature sets)
  stats       base model components + player and team stats; no crowd data at all
  full        stats + crowd levels and flow (ownership, net transfers, price change)
  stats_flow  stats + transfer flow only (no ownership levels: never "what is popular")
  overlay     same features as stats_flow, but learns only the correction to the base xP,
              so the structural model's spread at the top (captaincy) is kept
"""
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .panel import OUT, OUT_DIR

warnings.filterwarnings("ignore")
SEASON_ORDER = {"2023-24": 0, "2024-25": 1, "2025-26": 2, "2026-27": 3}

BASE = ["xp", "x_app", "x_goals", "x_assists", "x_cs", "x_gc", "x_saves", "x_dc", "x_bonus", "x_cards",
        "cs_prob", "att_factor", "n_fix", "home", "h", "p_play", "p60", "e_min", "xg90", "xa90", "dc_hit",
        "bonus90", "pos_code", "defcon_era"]
STATS = ["form", "min1", "min3", "min6", "start6", "full6", "xgi_pg3", "xgi_pg6", "xgi_pg_season", "thr_pg6",
         "crea_pg6", "infl_pg6", "ict_pg6", "bps_pg6", "bps_pg_season", "bonus_pg6", "pts4", "pts_pg_season",
         "dc_pg6", "rec_pg6", "saves_pg6", "fin_over", "ga_minus_xgi_pg6", "team_xgf6", "team_xga6",
         "xgi_p90_6", "value"]
FLOW = ["own_mom", "tb_share", "tin_rank", "tout_rank"]
LEVELS = ["own_rank", "log_sel", "value_chg"]
FEATURES = {"stats": BASE + STATS, "full": BASE + STATS + FLOW + LEVELS,
            "stats_flow": BASE + STATS + FLOW, "overlay": BASE + STATS + FLOW}
RESIDUAL = {"overlay"}
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=400,
              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
              n_estimators=500, verbose=-1, seed=7)
RETRAIN = 3


def load_panel(path=OUT):
    P = pd.read_parquet(path)
    P = P[P["pool"] & P["points"].notna()].copy()
    return prepare(P)


def prepare(P):
    P["pos_code"] = P["pos"].map({"GK": 0, "DEF": 1, "MID": 2, "FWD": 3})
    P["defcon_era"] = P["season"].isin(["2025-26", "2026-27"]).astype(int)
    P["s_idx"] = P["season"].map(SEASON_ORDER)
    return P


def fit_spec(train, features, residual, params=None):
    """Fit one model from an explicit spec (feature list, residual or not, LightGBM settings)."""
    m = lgb.LGBMRegressor(**(params or PARAMS))
    y = train["points"] - train["xp"] if residual else train["points"]
    m.fit(train[features], y)
    return m


def predict_spec(m, rows, features, residual):
    out = m.predict(rows[features])
    return out + rows["xp"].to_numpy() if residual else out


def fit(train, variant):
    return fit_spec(train, FEATURES[variant], variant in RESIDUAL)


def predict(m, rows, variant):
    return predict_spec(m, rows, FEATURES[variant], variant in RESIDUAL)


def walk_forward(P, test_seasons, variants, log=print):
    out = []
    for season in test_seasons:
        si = SEASON_ORDER[season]
        origins = sorted(P.loc[P["season"] == season, "origin"].unique())
        models = {}
        for i, g in enumerate(origins):
            if i % RETRAIN == 0:
                tr = P[(P["s_idx"] < si) | ((P["s_idx"] == si) & (P["gw"] < g))]
                models = {v: fit(tr, v) for v in variants}
            te = P[(P["season"] == season) & (P["origin"] == g)].copy()
            for v in variants:
                te["pred_" + v] = predict(models[v], te, v)
            out.append(te)
        log(f"walk-forward {season} done")
    res = pd.concat(out, ignore_index=True)
    res["pred_base"] = res["xp"]
    return res


def per_gw(res, col):
    """Per-gameweek rank IC (1 GW ahead) and top-20 average points."""
    one = res[res["h"] == 0]
    ic = one.groupby(["season", "origin"]).apply(lambda d: spearmanr(d[col], d["points"]).statistic)
    top = one.sort_values(col, ascending=False).groupby(["season", "origin"]).head(20)
    return ic, top.groupby(["season", "origin"])["points"].mean()


def metrics(res, col):
    one = res[res["h"] == 0]
    six = res.groupby(["season", "origin", "element"]).agg(pred=(col, "sum"), points=("points", "sum"),
                                                            n=("gw", "count")).reset_index()
    six = six[six["n"] == 6]
    ic, top = per_gw(res, col)
    ic6 = six.groupby(["season", "origin"]).apply(lambda d: spearmanr(d["pred"], d["points"]).statistic)
    top1 = one.sort_values(col, ascending=False).groupby(["season", "origin"]).head(1)["points"].mean()
    return {"rank_1gw": ic.mean(), "rank_6gw": ic6.mean(), "mae_1gw": (one[col] - one["points"]).abs().mean(),
            "top20_pts": top.mean(), "top1_pts": top1, "gws": len(ic)}


def paired(res, a, b):
    """b minus a per gameweek: rank IC and top-20 points, with t-statistics."""
    ia, ta = per_gw(res, a)
    ib, tb = per_gw(res, b)
    d, dt = (ib - ia).dropna(), (tb - ta).dropna()
    t = lambda x: x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 1 and x.std(ddof=1) > 0 else np.nan
    return {"d_rank": d.mean(), "t_rank": t(d), "won": (d > 0).mean(), "d_top20": dt.mean(), "t_top20": t(dt)}


def periods(res):
    """Evaluation periods. 2025/26 GW6-24 is where the base model's settings were tuned."""
    cur = res["season"] == "2026-27"
    last = res.loc[cur, "origin"].max() if cur.any() else None
    return {
        "2024-25 (all)": res["season"] == "2024-25",
        "2025-26 GW6-24 (base tuned here)": (res["season"] == "2025-26") & (res["origin"] <= 24),
        "2025-26 GW25-38 (untouched)": (res["season"] == "2025-26") & (res["origin"] >= 25),
        f"2026-27 GW6-{last} (untouched)": cur,
    }


def run(variants=("stats", "full", "stats_flow", "overlay"), log=print):
    P = load_panel()
    res = walk_forward(P, ["2024-25", "2025-26", "2026-27"], list(variants), log=log)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT_DIR / "walk_forward.parquet")
    return res
