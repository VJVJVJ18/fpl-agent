"""Shadow model: the research lab's best candidate, run live at every brief and scored at
every review, but never used for decisions until it passes the live gate and Vuk agrees.

Candidate (research/REPORT.md, 28 Nov 2026): the "overlay", a LightGBM model that learns the
current model's errors from player stats and transfer flow (no ownership levels). Walk-forward it
lifted rank IC by +0.02 to +0.05 in every period (t 3.9 to 6.8), but in same-state decision
tests the gain was +0.20 points per GW with a 90% interval of -0.79 to +1.19: not proven.

Live gate (checked by fpl/review.py): over at least 6 reviewed gameweeks, the shadow beats the
live model's rank correlation in at least 5, by +0.02 or more on average, without a lower
top-20 average. Then the review proposes promotion to Vuk with the numbers.

  python -m fpl.shadow     # train and log shadow projections for the next gameweek
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
CANDIDATE = "overlay"
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
    """Train the candidate on all finished data, score the live rows, log the next gameweek."""
    from research import combiner
    P = training_panel(log)
    model = combiner.fit(P, CANDIDATE)
    raw, teams, team_of = collector_raw(boot, fixtures)
    rows = live_features(proj, raw, teams, team_of, signal_origin(boot))
    rows["xp_shadow"] = combiner.predict(model, rows, CANDIDATE)
    nx = rows[rows["gw"] == nxt].groupby("element", as_index=False).agg(
        xp_live=("xp_live", "sum"), xp_shadow=("xp_shadow", "sum"), p_play=("p_play", "first"))
    nx["availability"] = nx["element"].map(availability).fillna(1.0)
    nx["xp_shadow_flagged"] = nx["xp_shadow"] * nx["availability"]
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
        return {"status": "logged", "file": str(path.relative_to(ROOT)), "players": len(nx)}
    except Exception as err:  # noqa: BLE001
        return {"status": f"failed: {type(err).__name__}: {err}"}


if __name__ == "__main__":
    from . import live
    proj, info, nxt, gws, boot, fixtures = live.projections()
    print(json.dumps(log_for_brief(proj, boot, fixtures, nxt), indent=1))
