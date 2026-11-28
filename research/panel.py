"""Build a point-in-time research panel: one row per (season, origin GW, player, target GW).

Everything in a row is known at the origin deadline:
  - base model projection for the target GW (existing fpl.model, walk-forward)
  - player signals from gameweeks before the origin (stats, minutes, FPL's ICT data)
  - crowd and price state at the origin deadline (ownership, net transfers, price)
Target: FPL points in the target GW.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from fpl import backtest, data, model  # noqa: E402

backtest.PREV.update({"2023-24": "2022-23", "2024-25": "2023-24"})
SEASONS = ["2023-24", "2024-25", "2025-26", data.CURRENT_SEASON]
HORIZON = 6
FIRST_ORIGIN = 6
OUT_DIR = REPO / "research" / "out"
OUT = OUT_DIR / "panel.parquet"

RAW_COLS = {"influence": "infl", "creativity": "crea", "threat": "thr", "ict_index": "ict",
            "transfers_balance": "tb", "transfers_in": "tin", "transfers_out": "tout",
            "recoveries": "rec", "tackles": "tkl", "clearances_blocks_interceptions": "cbi",
            "expected_goal_involvements": "xgi"}


def raw_gw_table(season):
    """Per player per GW (doubles summed) from the vaastav archive, with ICT, flow and price."""
    m = data._csv(season, "gws/merged_gw.csv").rename(columns=data.RENAME).rename(columns=RAW_COLS)
    m = m.rename(columns={"GW": "gw"})
    num = ["minutes", "starts", "goals", "assists", "xg", "xa", "xgi", "xgc", "cs", "gc", "saves", "bonus",
           "bps", "dc", "points", "infl", "crea", "thr", "ict", "rec", "tkl", "cbi"]
    for c in num + ["tb", "tin", "tout", "selected", "value"]:
        m[c] = pd.to_numeric(m[c], errors="coerce") if c in m else np.nan
    agg = {c: "sum" for c in num}
    agg.update({"tb": "max", "tin": "max", "tout": "max", "selected": "max", "value": "max",
                "position": "first", "fixture": "count"})
    t = m.groupby(["element", "gw"], as_index=False).agg(agg).rename(columns={"fixture": "n_played_fix"})
    t["pos"] = t["position"].map(data.POS)
    return t.drop(columns="position")


def team_gw_table(season):
    """Team xG for/against per GW (summed over players), for rolling team form."""
    m = data._csv(season, "gws/merged_gw.csv")
    teams = data._csv(season, "teams.csv")
    tid = dict(zip(teams["name"], teams["id"]))
    m["team_id"] = m["team"].map(tid)
    m["xg"] = pd.to_numeric(m["expected_goals"], errors="coerce")
    f = m.groupby(["GW", "fixture", "team_id", "opponent_team"], as_index=False)["xg"].sum()
    f = f.merge(f[["fixture", "team_id", "xg"]].rename(columns={"team_id": "opponent_team", "xg": "xga"}),
                on=["fixture", "opponent_team"], how="left")
    return f.groupby(["GW", "team_id"], as_index=False)[["xg", "xga"]].sum().rename(
        columns={"GW": "gw", "team_id": "team", "xg": "txgf", "xga": "txga"})


def roll(t, g, k, cols, per="game"):
    """Sum of cols over GWs g-k..g-1, divided by k (per game) or per 90 minutes."""
    w = t[(t["gw"] >= g - k) & (t["gw"] < g)]
    s = w.groupby("element")[list(dict.fromkeys(cols + ["minutes"]))].sum()
    if per == "game":
        return s[cols] / k
    return s[cols].div(s["minutes"].clip(lower=1) / 90, axis=0)


def signals(raw, teams, elem_team, g):
    """Player signals known at the deadline of GW g."""
    hist = raw[raw["gw"] < g]
    cur = raw[raw["gw"] == g].set_index("element")
    first = raw.sort_values("gw").groupby("element")["value"].first()
    out = pd.DataFrame(index=hist["element"].unique())
    out.index.name = "element"
    for k in (1, 3, 6):
        out[f"min{k}"] = roll(raw, g, k, ["minutes"])["minutes"]
    out["start6"] = roll(raw, g, 6, ["starts"])["starts"]
    full = hist[(hist["gw"] >= g - 6)].assign(f90=lambda d: (d["minutes"] >= 89).astype(float))
    out["full6"] = full.groupby("element")["f90"].sum() / 6
    for k in (3, 6):
        pg = roll(raw, g, k, ["xg", "xa", "xgi", "thr", "crea", "infl", "ict", "bps", "bonus", "points", "dc", "rec", "saves"])
        for c in pg:
            out[f"{c}_pg{k}"] = pg[c]
    p90 = roll(raw, g, 6, ["xg", "xa", "xgi", "thr", "crea", "bps", "dc"], per="90")
    for c in p90:
        out[f"{c}_p90_6"] = p90[c]
    season = hist.groupby("element")[["xg", "xa", "goals", "assists", "points", "minutes", "xgi", "bps"]].sum()
    n_gw = max(g - 1, 1)
    out["pts_pg_season"] = season["points"] / n_gw
    out["xgi_pg_season"] = season["xgi"] / n_gw
    out["bps_pg_season"] = season["bps"] / n_gw
    out["fin_over"] = (season["goals"] - season["xg"])          # finishing luck so far
    out["ast_over"] = (season["assists"] - season["xa"])
    out["ga_minus_xgi_pg6"] = roll(raw, g, 6, ["goals", "assists", "xgi"]).eval("goals + assists - xgi")
    out["pts4"] = roll(raw, g, 4, ["points"])["points"]
    # price and crowd at the GW g deadline
    out["value"] = cur["value"].reindex(out.index)
    out["value_chg"] = (cur["value"] - first).reindex(out.index)
    sel = cur["selected"].reindex(out.index)
    out["own_rank"] = sel.rank(pct=True)
    out["log_sel"] = np.log1p(sel)
    flow_scale = cur["tb"].abs().sum() or 1.0
    out["tb_share"] = (cur["tb"] / flow_scale).reindex(out.index)
    out["tin_rank"] = cur["tin"].reindex(out.index).rank(pct=True)
    out["tout_rank"] = cur["tout"].reindex(out.index).rank(pct=True)
    out["tb_rank"] = cur["tb"].reindex(out.index).rank(pct=True)
    prev_sel = raw[raw["gw"] == g - 1].set_index("element")["selected"].reindex(out.index)
    out["own_mom"] = np.log1p(sel) - np.log1p(prev_sel)
    # team form (rolling 6 GWs)
    tw = teams[(teams["gw"] >= g - 6) & (teams["gw"] < g)].groupby("team")[["txgf", "txga"]].sum() / 6
    tm = out.index.map(elem_team)
    out["team_xgf6"] = tw["txgf"].reindex(tm).to_numpy()
    out["team_xga6"] = tw["txga"].reindex(tm).to_numpy()
    return out.reset_index()


def build(seasons=None):
    params = model.load_params()
    seasons = seasons or SEASONS
    frames = []
    for season in seasons:
        s = backtest.Season(season)
        raw = raw_gw_table(season)
        teams = team_gw_table(season)
        last_raw = int(raw["gw"].max())
        last = min(s.last_gw, last_raw)
        srt = s.df.sort_values("gw")
        for g in range(FIRST_ORIGIN, last + 1):
            elem_team = srt[srt["gw"] < g].groupby("element")["team"].last().to_dict()
            pr = backtest.predict(s, g, params, horizon=HORIZON)
            sig = signals(raw, teams, elem_team, g)
            pr = pr.merge(sig, on="element", how="left")
            pr["season"] = season
            pr["h"] = pr["gw"] - pr["origin"]
            pr["opp"] = pr["opp"].map(lambda v: v[0] if isinstance(v, list) and v else np.nan)
            pr["home"] = pr["home"].map(lambda v: float(v[0]) if isinstance(v, list) and v else np.nan)
            pos = s.df.drop_duplicates("element").set_index("element")["pos"]
            pr["pos"] = pr["element"].map(pos)
            frames.append(pr)
        print("panel", season, "origins", FIRST_ORIGIN, "-", last, flush=True)
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop(columns=["fpl_xp"], errors="ignore")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT)
    print(panel.shape)
    return panel


if __name__ == "__main__":
    build()
