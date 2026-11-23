"""Expected points (xP) per player per gameweek.

For each player and fixture:

  xP = appearance + goals + assists + clean sheet - goals conceded
       + saves + defensive contribution + bonus - cards

  appearance  = P(play) + P(60+)                       (1 pt for playing, 1 more at 60')
  goals       = goal pts(pos) x xG/90 x E[min]/90 x fixture attack factor
  assists     = 3 x xA/90 x E[min]/90 x fixture attack factor
  clean sheet = CS pts(pos) x P(60+) x exp(-lambda_against)
  conceded    = -E[floor(GC/2)] x E[min]/90, with GC ~ Poisson(lambda_against)  (GK, DEF)
  saves       = E[floor(S/3)], S ~ Poisson(saves/90 x E[min]/90 x opponent attack)  (GK)
  DefCon      = 2 x P(hit threshold | 60+) x P(60+)
  bonus       = bonus/90 x E[min]/90;  cards = -yellow/90 x E[min]/90

Minutes come from recent appearances (decayed). Per-90 rates are shrunk toward
the player's last-season rate, which is itself shrunk toward the position
average. Team attack and defence ratings come from xG for and against, adjusted
for opponent strength and home advantage, and shrunk toward last season.
All tunable settings live in PARAMS; the backtest picks their values.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .scoring import DEFAULT as DEFAULT_SCORING

PARAMS_FILE = Path(__file__).resolve().parent.parent / "params.json"
DEFAULTS = {
    "min_window": 6,       # gameweeks of minutes history used
    "min_decay": 0.75,     # weight per gameweek back, for minutes
    "rate_decay": 0.97,    # weight per gameweek back, for per-90 rates
    "k_rate": 6.0,         # shrinkage of this season's rates toward the prior, in 90s
    "k_prior": 8.0,        # shrinkage of last season's rate toward the position mean, in 90s
    "team_decay": 0.95,    # weight per gameweek back, for team ratings
    "k_team": 4.0,         # shrinkage of team ratings toward last season, in matches
    "home": 1.10,          # xG multiplier at home (1/home away)
    "promoted_att": 0.85,  # prior attack rating for a team not in last season's data
    "promoted_def": 1.15,  # prior defence rating (xG conceded) for such a team
    "k_dc": 6.0,           # shrinkage of DefCon hit rate, in 60-minute appearances
    "att_power": 1.0,      # how strongly fixture difficulty scales attacking returns
}
RATES = ["xg", "xa", "bonus", "saves", "yc"]
SAVE_KS = np.arange(0, 41)


def load_params():
    p = dict(DEFAULTS)
    if PARAMS_FILE.exists():
        p.update(json.loads(PARAMS_FILE.read_text())["params"])
    return p


def load_calibration():
    """Coefficients fitted by the backtest; identity if none yet."""
    if PARAMS_FILE.exists():
        cal = json.loads(PARAMS_FILE.read_text()).get("calibration")
        if cal:
            return cal
    return {"xp_only": [0.0, 1.0], "with_own": [0.0, 1.0, 0.0]}


def calibrate(proj, own=None, cal=None):
    """Turn raw xP into calibrated xP.

    calibrated = a x P(play) + b x raw xP [+ c x ln(1 + ownership %) x P(play)]

    The backtest found the raw model slightly overconfident at the top end, and
    that ownership carries information the stats miss (mostly minutes and news),
    so it is used as a small adjustment when available. Rankings barely change;
    levels become trustworthy for hit and chip decisions.
    """
    cal = cal or load_calibration()
    out = proj.copy()
    out["xp_raw"] = out["xp"]
    if own is not None:
        a, b, c = cal["with_own"]
        o = out["element"].map(own).fillna(0).clip(0, 100)
        out["xp"] = a * out["p_play"] + b * out["xp_raw"] + c * np.log1p(o) * out["p_play"]
    else:
        a, b = cal["xp_only"]
        out["xp"] = a * out["p_play"] + b * out["xp_raw"]
    return out


def _w(gw, g, decay):
    return decay ** (g - 1 - gw)


def _team_table(df):
    """One row per team per fixture: xG for and against."""
    tf = df.groupby(["gw", "fixture", "team", "team_code", "opp", "opp_code", "home"],
                    as_index=False)["xg"].sum().rename(columns={"xg": "xgf"})
    opp = tf[["fixture", "team", "xgf"]].rename(columns={"team": "opp", "xgf": "xga"})
    return tf.merge(opp, on=["fixture", "opp"], how="left")


def team_ratings(hist, prev, g, p):
    """Attack and defence ratings (1.0 = league average) for this season's teams."""
    pt = _team_table(prev) if len(prev) else pd.DataFrame()
    L_prev = pt["xgf"].mean() if len(pt) else 1.35
    prior_a = (pt.groupby("team_code")["xgf"].mean() / L_prev).to_dict() if len(pt) else {}
    prior_d = (pt.groupby("team_code")["xga"].mean() / L_prev).to_dict() if len(pt) else {}

    tf = _team_table(hist) if len(hist) else pd.DataFrame()
    L = tf["xgf"].mean() if len(tf) else L_prev
    teams = pd.concat([hist[["team", "team_code"]], hist[["opp", "opp_code"]].set_axis(["team", "team_code"], axis=1)]
                      ).drop_duplicates("team") if len(hist) else pd.DataFrame(columns=["team", "team_code"])
    a = {t: prior_a.get(c, p["promoted_att"]) for t, c in zip(teams["team"], teams["team_code"])}
    d = {t: prior_d.get(c, p["promoted_def"]) for t, c in zip(teams["team"], teams["team_code"])}
    if len(tf):
        tf = tf.dropna(subset=["xga"]).copy()
        tf["w"] = _w(tf["gw"], g, p["team_decay"])
        hf = np.where(tf["home"], p["home"], 1 / p["home"])
        for _ in range(3):  # adjust for opponent strength
            a_est = tf["xgf"] / (L * tf["opp"].map(d) * hf)
            d_est = tf["xga"] / (L * tf["opp"].map(a) / hf)
            new_a, new_d = {}, {}
            for t, grp in tf.assign(a_est=a_est, d_est=d_est).groupby("team"):
                sw = grp["w"].sum()
                new_a[t] = ((grp["w"] * grp["a_est"]).sum() + p["k_team"] * a[t]) / (sw + p["k_team"])
                new_d[t] = ((grp["w"] * grp["d_est"]).sum() + p["k_team"] * d[t]) / (sw + p["k_team"])
            a.update(new_a)
            d.update(new_d)
    return a, d, L


def _prior_rates(prev, hist, p, sc):
    """Per-90 prior for each player code, from last season shrunk to the position mean."""
    base = prev if len(prev) else hist
    pos_mean = {}
    for pos, grp in base.groupby("pos"):
        n90 = grp["minutes"].sum() / 90
        pos_mean[pos] = {r: grp[r].sum() / n90 if n90 else 0 for r in RATES}
        app60 = (grp["minutes"] >= 60)
        hits = app60 & (grp["dc"].fillna(-1) >= sc["dc_threshold"][pos])
        pos_mean[pos]["dc_hit"] = hits.sum() / app60.sum() if grp["dc"].notna().any() and app60.sum() else np.nan
    # DefCon did not exist before 2025/26: fall back to this season's position mean.
    for pos, grp in hist.groupby("pos"):
        if np.isnan(pos_mean.get(pos, {}).get("dc_hit", np.nan)):
            app60 = grp["minutes"] >= 60
            hits = app60 & (grp["dc"].fillna(-1) >= sc["dc_threshold"][pos])
            pos_mean.setdefault(pos, {})["dc_hit"] = hits.sum() / app60.sum() if app60.sum() else 0.1
    priors = {}
    if len(prev):
        agg = prev.assign(app60=prev["minutes"] >= 60,
                          hit=(prev["minutes"] >= 60) & (prev["dc"].fillna(-1) >= prev["pos"].map(sc["dc_threshold"])))
        agg = agg.groupby(["code", "pos"], as_index=False).agg(
            minutes=("minutes", "sum"), app60=("app60", "sum"), hit=("hit", "sum"),
            dc_known=("dc", lambda s: s.notna().any()), **{r: (r, "sum") for r in RATES})
        for row in agg.itertuples():
            pm = pos_mean.get(row.pos)
            if pm is None:
                continue
            n90 = row.minutes / 90
            pr = {r: (getattr(row, r) + p["k_prior"] * pm[r]) / (n90 + p["k_prior"]) for r in RATES}
            pr["dc_hit"] = ((row.hit + p["k_dc"] * pm["dc_hit"]) / (row.app60 + p["k_dc"])
                            if row.dc_known else pm["dc_hit"])
            priors[row.code] = pr
    return priors, pos_mean


def player_features(hist, prev, g, p, universe, sc=DEFAULT_SCORING):
    """Minutes and per-90 rates as known before gameweek g, for every player in universe."""
    priors, pos_mean = _prior_rates(prev, hist, p, sc)
    f = universe[["element", "code", "pos", "team"]].drop_duplicates("element").set_index("element")

    win = hist[hist["gw"] >= g - p["min_window"]]
    if len(win):
        w = _w(win["gw"], g, p["min_decay"])
        m = win["minutes"]
        mins = pd.DataFrame({"element": win["element"], "w": w, "play": w * (m > 0),
                             "p60": w * (m >= 60), "emin": w * m}).groupby("element").sum()
        f["p_play"] = (mins["play"] / mins["w"]).reindex(f.index).fillna(0)
        f["p60"] = (mins["p60"] / mins["w"]).reindex(f.index).fillna(0)
        f["e_min"] = (mins["emin"] / mins["w"]).reindex(f.index).fillna(0)
    else:  # first gameweek: no information on minutes yet
        f["p_play"], f["p60"], f["e_min"] = 0.0, 0.0, 0.0

    if len(hist):
        w = _w(hist["gw"], g, p["rate_decay"])
        app60 = hist["minutes"] >= 60
        hit = app60 & (hist["dc"].fillna(-1) >= hist["pos"].map(sc["dc_threshold"]))
        sums = pd.DataFrame({"element": hist["element"], "n90": w * hist["minutes"] / 90,
                             "app60": w * app60, "hit": w * hit,
                             **{r: w * hist[r].fillna(0) for r in RATES}}).groupby("element").sum()
        sums = sums.reindex(f.index).fillna(0)
    else:
        sums = pd.DataFrame(0.0, index=f.index, columns=["n90", "app60", "hit"] + RATES)
    for r in RATES + ["dc_hit"]:
        prior = np.array([priors.get(c, pos_mean.get(pos, {})).get(r, 0.0) for c, pos in zip(f["code"], f["pos"])],
                         dtype=float)
        if r == "dc_hit":
            f[r] = (sums["hit"] + p["k_dc"] * prior) / (sums["app60"] + p["k_dc"])
        else:
            f[r + "90"] = (sums[r] + p["k_rate"] * prior) / (sums["n90"] + p["k_rate"])
    return f.reset_index()


def target_fixtures(fixtures, gws):
    """One row per team per fixture in the target gameweeks (doubles give two rows)."""
    fx = fixtures[fixtures["event"].isin(gws)]
    h = pd.DataFrame({"gw": fx["event"], "fixture": fx["id"], "team": fx["team_h"], "opp": fx["team_a"], "home": True})
    a = pd.DataFrame({"gw": fx["event"], "fixture": fx["id"], "team": fx["team_a"], "opp": fx["team_h"], "home": False})
    return pd.concat([h, a], ignore_index=True).astype({"gw": int})


def _e_floor_half(lam):
    return lam / 2 - (1 - np.exp(-2 * lam)) / 4


def _e_floor_third(lam):
    pmf = poisson.pmf(SAVE_KS[None, :], np.asarray(lam)[:, None])
    return (pmf * (SAVE_KS // 3)[None, :]).sum(axis=1)


def project(hist, prev, fixtures, gws, universe, p=None, sc=DEFAULT_SCORING):
    """xP for each player in universe for each gameweek in gws, using only data in hist.

    Returns one row per player per gameweek with xP and its parts.
    """
    p = p or load_params()
    g = min(gws)
    feats = player_features(hist, prev, g, p, universe, sc)
    a, d, L = team_ratings(hist, prev, g, p)
    tf = target_fixtures(fixtures, gws)
    rows = feats.merge(tf, on="team", how="inner")
    if rows.empty:
        return pd.DataFrame(columns=["element", "gw", "xp"])
    hf = np.where(rows["home"], p["home"], 1 / p["home"])
    a_opp = rows["opp"].map(a).fillna(p["promoted_att"])
    d_opp = rows["opp"].map(d).fillna(p["promoted_def"])
    d_own = rows["team"].map(d).fillna(p["promoted_def"])
    lam_against = L * a_opp * d_own / hf
    att = (d_opp * hf) ** p["att_power"]
    share = rows["e_min"] / 90
    pos = rows["pos"]

    rows["x_app"] = rows["p_play"] + rows["p60"]
    rows["x_goals"] = pos.map(sc["goals"]) * rows["xg90"] * share * att
    rows["x_assists"] = sc["assist"] * rows["xa90"] * share * att
    rows["x_cs"] = pos.map(sc["cs"]) * rows["p60"] * np.exp(-lam_against)
    rows["x_gc"] = pos.map(sc["gc_per2"]) * _e_floor_half(lam_against) * share
    rows["x_saves"] = np.where(pos == "GK", sc["save_per3"] * _e_floor_third(rows["saves90"] * share * a_opp / hf), 0.0)
    rows["x_dc"] = pos.map(sc["dc_points"]) * rows["dc_hit"] * rows["p60"]
    rows["x_bonus"] = rows["bonus90"] * share
    rows["x_cards"] = sc["yc"] * rows["yc90"] * share
    parts = ["x_app", "x_goals", "x_assists", "x_cs", "x_gc", "x_saves", "x_dc", "x_bonus", "x_cards"]
    rows["xp"] = rows[parts].sum(axis=1)
    rows["cs_prob"] = np.exp(-lam_against)
    rows["att_factor"] = att
    keep = ["element", "gw"] + parts + ["xp"]
    out = rows[keep].groupby(["element", "gw"], as_index=False).sum()
    info = rows.groupby(["element", "gw"], as_index=False).agg(
        n_fix=("fixture", "count"), cs_prob=("cs_prob", "mean"), att_factor=("att_factor", "mean"),
        opp=("opp", lambda s: list(s)), home=("home", lambda s: list(s)))
    out = out.merge(info, on=["element", "gw"])
    return out.merge(feats[["element", "p_play", "p60", "e_min", "xg90", "xa90", "dc_hit", "bonus90"]], on="element")
