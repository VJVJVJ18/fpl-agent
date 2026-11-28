"""Shadow book: candidate models run live at every brief and scored at every review, but never
used for decisions until one passes the live gate and Vuk agrees.

Each version is frozen: its feature list and LightGBM settings are written out in VERSIONS, so
later research changes cannot alter a version that is already building a live record. New
versions are added by the improvement loop (BRIEF.md section 7) only after they pass the
research gate; at most MAX_ACTIVE run at once.

v1 (28 Nov 2026, live from GW13): the research "overlay", a LightGBM model that learns the
current model's errors from player stats and transfer flow (no ownership levels). Walk-forward
it lifted rank IC by +0.02 to +0.05 in every period (t 3.9 to 6.8); in same-state decision tests
the gain was +0.20 points per GW with a 90% interval of -0.77 to +1.15: not proven.

Live gate (fpl/review.py), per version: at least 6 reviewed gameweeks, ahead of the live model's
rank correlation in at least 5 of every 6, by +0.02 or more on average, with a top-20 average no
lower. Decision point agreed with Vuk: the GW18 review, before the GW19 deadline.

  python -m fpl.shadow     # train every active version and log the next gameweek
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data

ROOT = Path(__file__).resolve().parent.parent
CACHE = data.CACHE / "shadow_panel.parquet"
MAX_AGE_H = 72
MAX_ACTIVE = 3
V1_FEATURES = [
    "xp", "x_app", "x_goals", "x_assists", "x_cs", "x_gc", "x_saves", "x_dc", "x_bonus", "x_cards",
    "cs_prob", "att_factor", "n_fix", "home", "h", "p_play", "p60", "e_min", "xg90", "xa90", "dc_hit",
    "bonus90", "pos_code", "defcon_era", "form", "min1", "min3", "min6", "start6", "full6", "xgi_pg3",
    "xgi_pg6", "xgi_pg_season", "thr_pg6", "crea_pg6", "infl_pg6", "ict_pg6", "bps_pg6", "bps_pg_season",
    "bonus_pg6", "pts4", "pts_pg_season", "dc_pg6", "rec_pg6", "saves_pg6", "fin_over", "ga_minus_xgi_pg6",
    "team_xgf6", "team_xga6", "xgi_p90_6", "value", "own_mom", "tb_share", "tin_rank", "tout_rank"]
V1_PARAMS = {"objective": "regression", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 400,
             "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0,
             "n_estimators": 500, "verbose": -1, "seed": 7}
VERSIONS = {
    "v1": {"added": "2026-11-28", "first_gw": 13, "residual": True, "features": V1_FEATURES,
           "params": V1_PARAMS, "desc": "overlay: stats + transfer flow, learns the live model's errors"},
}
ACTIVE = ["v1"]
assert len(ACTIVE) <= MAX_ACTIVE
RAW_MAP = {"goals_scored": "goals", "expected_goals": "xg", "expected_assists": "xa",
           "expected_goal_involvements": "xgi", "expected_goals_conceded": "xgc", "clean_sheets": "cs",
           "goals_conceded": "gc", "defensive_contribution": "dc", "total_points": "points",
           "influence": "infl", "creativity": "crea", "threat": "thr", "ict_index": "ict",
           "recoveries": "rec", "tackles": "tkl", "clearances_blocks_interceptions": "cbi"}
RAW_COLS = ["minutes", "starts", "goals", "assists", "xg", "xa", "xgi", "xgc", "cs", "gc", "saves", "bonus",
            "bps", "dc", "points", "infl", "crea", "thr", "ict", "rec", "tkl", "cbi"]


def training_panel(log=print):
    """The research panel (all seasons), rebuilt when older than MAX_AGE_H hours."""
    from research import combiner, panel
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < MAX_AGE_H * 3600:
        raw = pd.read_parquet(CACHE)
    else:
        log("shadow: building training panel")
        raw = panel.build()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        raw.to_parquet(CACHE)
    return combiner.prepare(raw[raw["pool"] & raw["points"].notna()].copy())


def signal_origin(boot):
    """First gameweek that is not finished: the origin the research features are defined at."""
    return max([e["id"] for e in boot["events"] if e["finished"]] or [0]) + 1


def collector_raw(boot, fixtures, base=data.COLLECTOR):
    """This season per player per finished GW, in the research panel's column names, plus the
    deadline-time crowd state (ownership, net transfers, price) for the next gameweek."""
    els = pd.DataFrame(boot["elements"])
    pos = els.set_index("id")["element_type"].map(data.POS_BY_TYPE)
    finished = [e["id"] for e in boot["events"] if e["finished"]]
    rows = []
    for gw in finished:
        live = data._json(base, f"live/{gw}.json")
        st = pd.DataFrame([{"element": x["id"], **x["stats"]} for x in live["elements"]]).rename(columns=RAW_MAP)
        st["gw"] = gw
        rows.append(st)
    raw = pd.concat(rows, ignore_index=True)
    for c in RAW_COLS:
        raw[c] = pd.to_numeric(raw[c], errors="coerce") if c in raw else np.nan
    raw = raw[["element", "gw"] + RAW_COLS].copy()
    # The research panel's origin g means "every GW before g is finished". If the current GW is
    # still being played, that origin is the current GW, not the next one.
    nxt = signal_origin(boot)
    total = boot.get("total_players") or 1
    sel = els["selected_by_percent"].astype(float).to_numpy() / 100 * total
    net = (els["transfers_in_event"] - els["transfers_out_event"]).astype(float).to_numpy()
    crowd = pd.DataFrame({"element": els["id"], "gw": nxt, "selected": sel, "tb": net,
                          "tin": els["transfers_in_event"].astype(float), "tout": els["transfers_out_event"].astype(float),
                          "value": els["now_cost"].astype(float)})
    before = crowd.assign(gw=nxt - 1, selected=np.clip(sel - net, 1, None))[["element", "gw", "selected"]]
    raw = raw.merge(pd.concat([crowd, before]).groupby(["element", "gw"], as_index=False).last(),
                    on=["element", "gw"], how="outer")
    first_price = (els["now_cost"] - els["cost_change_start"]).astype(float)
    first = pd.DataFrame({"element": els["id"], "gw": 0, "value": first_price})  # price at GW1, for value_chg
    raw = pd.concat([raw, first], ignore_index=True)
    raw["pos"] = raw["element"].map(pos)
    for c in ["tb", "tin", "tout", "selected", "value"]:
        if c not in raw:
            raw[c] = np.nan
    fx = fixtures.dropna(subset=["event"])
    team_of = els.set_index("id")["team"]
    raw["team_id"] = raw["element"].map(team_of)
    tx = raw[raw["gw"].isin(finished)].groupby(["gw", "team_id"], as_index=False)["xg"].sum()
    opp = pd.concat([pd.DataFrame({"gw": fx["event"].astype(int), "team_id": fx["team_h"], "opp": fx["team_a"]}),
                     pd.DataFrame({"gw": fx["event"].astype(int), "team_id": fx["team_a"], "opp": fx["team_h"]})])
    t = opp.merge(tx.rename(columns={"team_id": "opp", "xg": "txga"}), on=["gw", "opp"], how="left")
    teams = (t.groupby(["gw", "team_id"], as_index=False)["txga"].sum()
             .merge(tx.rename(columns={"xg": "txgf"}), on=["gw", "team_id"], how="left")
             .rename(columns={"team_id": "team"}))
    return raw.drop(columns="team_id"), teams[["gw", "team", "txgf", "txga"]], team_of.to_dict()


def live_features(proj, raw, teams, team_of, origin):
    """Feature rows at the research origin (first unfinished GW), matching the panel's columns."""
    from research import combiner, panel
    sig = panel.signals(raw, teams, team_of, origin)
    rows = proj.copy()
    rows["xp"] = rows["xp_raw"]               # the model was trained on raw (uncalibrated) xP
    rows["h"] = (rows["gw"] - origin).clip(upper=5)
    rows["opp"] = rows["opp"].map(lambda v: v[0] if isinstance(v, list) and v else np.nan)
    rows["home"] = rows["home"].map(lambda v: float(v[0]) if isinstance(v, list) and v else np.nan)
    rows = rows.merge(sig, on="element", how="left")
    rows["pos"] = rows["element"].map(raw.drop_duplicates("element").set_index("element")["pos"])
    rows["season"] = data.CURRENT_SEASON
    rows["form"] = rows["pts4"] * rows["n_fix"]
    return combiner.prepare(rows)


def run(proj, boot, fixtures, nxt, availability, log=print):
    """Train every active version on all finished data, score the live rows, log the next GW."""
    from research import combiner
    P = training_panel(log)
    raw, teams, team_of = collector_raw(boot, fixtures)
    rows = live_features(proj, raw, teams, team_of, signal_origin(boot))
    nx = rows[rows["gw"] == nxt].groupby("element", as_index=False).agg(
        xp_live=("xp_live", "sum"), p_play=("p_play", "first"))
    nx["availability"] = nx["element"].map(availability).fillna(1.0)
    for v in ACTIVE:
        spec = VERSIONS[v]
        model = combiner.fit_spec(P, spec["features"], spec["residual"], spec["params"])
        rows[f"xp_{v}"] = combiner.predict_spec(model, rows, spec["features"], spec["residual"])
        per = rows[rows["gw"] == nxt].groupby("element")[f"xp_{v}"].sum()
        nx[f"xp_{v}"] = nx["element"].map(per)
        nx[f"xp_{v}_flagged"] = nx[f"xp_{v}"] * nx["availability"]
    nx.insert(1, "gw", nxt)
    nx["made_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    nx["trained_rows"] = len(P)
    path = ROOT / "predictions" / data.CURRENT_SEASON / f"gw{nxt}_shadow.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.round(4).to_csv(path, index=False)
    return path, nx


def log_for_brief(proj, boot, fixtures, nxt, log=print):
    """Called by brief_data --save. Never raises: the brief must not depend on the shadow."""
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        return {"status": "skipped: pip install -r research/requirements.txt"}
    try:
        els = pd.DataFrame(boot["elements"]).set_index("id")
        chance = els["chance_of_playing_next_round"].astype(float) / 100
        status = els["status"]
        availability = chance.where(chance.notna(), np.where(status.isin({"i", "s", "u", "n"}), 0.0, 1.0)).to_dict()
        p = proj.assign(xp_live=proj["xp"])
        path, nx = run(p, boot, fixtures, nxt, availability, log)
        return {"status": "logged", "file": str(path.relative_to(ROOT)), "players": len(nx), "versions": ACTIVE}
    except Exception as err:  # noqa: BLE001
        return {"status": f"failed: {type(err).__name__}: {err}"}


if __name__ == "__main__":
    from . import live
    proj, info, nxt, gws, boot, fixtures = live.projections()
    print(json.dumps(log_for_brief(proj, boot, fixtures, nxt), indent=1))
