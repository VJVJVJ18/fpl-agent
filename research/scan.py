"""Signal scan: how much does each signal, known at the deadline, tell us about points?

IC    Spearman rank correlation between the signal and points that gameweek, across the
      decision pool (players who played in the last 4 GWs), computed per gameweek and averaged.
rIC   Pearson correlation between the signal and what the base model got wrong (points minus
      a per-gameweek linear fit on base xP). Positive = information the model lacks. Pearson,
      not rank: FPL points are so skewed that rank residuals mislead.
t     mean / sd x sqrt(gameweeks). |t| above 3 is a signal worth testing in a model.
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

SIGNALS = {
    "xp": "base model xP",
    "min1": "minutes last GW", "start6": "starts, last 6 GWs", "pts4": "points per GW, last 4",
    "xgi_pg3": "xG+xA per GW, last 3", "xgi_p90_6": "xG+xA per 90, last 6", "thr_pg6": "FPL threat per GW, last 6",
    "bps_pg6": "BPS per GW, last 6", "dc_pg6": "defensive contributions per GW, last 6",
    "fin_over": "goals minus xG, season", "ga_minus_xgi_pg6": "goals+assists minus xGI, last 6",
    "team_xga6": "team xG conceded, last 6", "cs_prob": "model clean-sheet probability",
    "value": "price", "value_chg": "price change since GW1",
    "own_rank": "ownership level (rank)", "own_mom": "ownership change into the deadline",
    "tb_share": "net transfers (share of all flow)", "tin_rank": "transfers in (rank)",
    "tout_rank": "transfers out (rank)",
}


def _t(x):
    x = np.asarray(x, dtype=float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 else np.nan


def scan(P):
    one = P[(P["h"] == 0) & P["pool"] & P["points"].notna()].copy()

    def resid(d):
        b, a = np.polyfit(d["xp"], d["points"], 1)
        return d["points"] - (a + b * d["xp"])
    one["resid"] = one.groupby(["season", "origin"], group_keys=False).apply(resid)
    rows = []
    for sig, label in SIGNALS.items():
        ic, ric = [], []
        for _, d in one.groupby(["season", "origin"]):
            x = d[[sig, "points", "resid"]].dropna()
            if len(x) > 30 and x[sig].nunique() > 5:
                ic.append(spearmanr(x[sig], x["points"]).statistic)
                ric.append(np.corrcoef(x[sig], x["resid"])[0, 1] if sig != "xp" else np.nan)
        if len(ic) < 5:
            continue
        rows.append({"signal": label, "IC": np.mean(ic), "IC t": _t(ic), "rIC": np.nanmean(ric) if sig != "xp" else np.nan,
                     "rIC t": _t(ric) if sig != "xp" else np.nan, "GWs": len(ic)})
    return pd.DataFrame(rows).sort_values("IC", ascending=False)


def flow_quintiles(P):
    """Players the model rates 90%+ to play, by ownership change into the deadline."""
    one = P[(P["h"] == 0) & P["pool"] & P["points"].notna() & (P["p_play"] >= 0.9)].copy()
    one["q"] = pd.qcut(one["own_mom"].rank(method="first"), 5,
                       labels=["most sold", "2", "3", "4", "most bought"])
    t = one.groupby("q", observed=True).agg(players=("xp", "size"), projected=("xp", "mean"),
                                            scored=("points", "mean"), blank_rate=("points", lambda s: (s == 0).mean()))
    t["scored minus projected"] = t["scored"] - t["projected"]
    return t
