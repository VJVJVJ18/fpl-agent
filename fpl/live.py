"""Projections for the upcoming gameweeks, from the collector's latest data.

Usage:
  python -m fpl.live            # next gameweek and the 5 after it; prints the top players
  python -m fpl.live --save     # also writes predictions/<season>/gw<n>.csv for the review loop

Availability: for the next gameweek, xP is scaled by FPL's chance_of_playing_next_round
(0 for injured or suspended players with no chance given). Later gameweeks are not
scaled: the brief adjusts them from press conferences and news.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, model, scoring

ROOT = Path(__file__).resolve().parent.parent
HORIZON = 6
OUT_STATUSES = {"i", "s", "u", "n"}


def projections(horizon=HORIZON):
    cur, boot, fixtures = data.load_current_season()
    prev = data.load_past_season("2025-26")
    sc = scoring.from_bootstrap(boot)
    nxt = next(e["id"] for e in boot["events"] if e["is_next"])
    gws = [g for g in range(nxt, nxt + horizon) if g <= 38]
    els = pd.DataFrame(boot["elements"])
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}
    universe = pd.DataFrame({"element": els["id"], "code": els["code"],
                             "pos": els["element_type"].map(data.POS_BY_TYPE), "team": els["team"]})
    fx = fixtures[["id", "event", "team_h", "team_a"]].dropna(subset=["event"]).astype({"event": int})
    proj = model.project(cur, prev, fx, gws, universe, model.load_params(), sc)
    own = els.set_index("id")["selected_by_percent"].astype(float)
    # No ownership in xP: the research lab found no out-of-sample gain from it (research/REPORT.md),
    # and Vuk's rule is that what other managers own is never evidence of points.
    proj = model.calibrate(proj)

    # Availability for the next gameweek only.
    chance = els.set_index("id")["chance_of_playing_next_round"]
    status = els.set_index("id")["status"]
    factor = chance.astype(float) / 100
    factor = factor.where(factor.notna(), np.where(status.isin(OUT_STATUSES), 0.0, 1.0))
    nxt_rows = proj["gw"] == nxt
    proj.loc[nxt_rows, "xp"] *= proj.loc[nxt_rows, "element"].map(factor).fillna(1.0)

    info = pd.DataFrame({
        "element": els["id"], "name": els["web_name"], "team": els["team"].map(teams),
        "pos": els["element_type"].map(data.POS_BY_TYPE), "price": els["now_cost"] / 10,
        "own": own.values, "status": els["status"], "chance": els["chance_of_playing_next_round"],
        "news": els["news"], "pens": els["penalties_order"],
    })
    return proj, info, nxt, gws, boot, fixtures


def wide(proj, info, gws, decay=0.9):
    """One row per player: xP per gameweek, the 6-GW total and a decayed total."""
    t = proj.pivot_table(index="element", columns="gw", values="xp", aggfunc="sum").reindex(columns=gws).fillna(0)
    t.columns = [f"gw{g}" for g in gws]
    t["xp_total"] = t.sum(axis=1)
    t["xp_decayed"] = sum(t[f"gw{g}"] * decay ** i for i, g in enumerate(gws))
    feats = proj.drop_duplicates("element").set_index("element")[["p_play", "p60", "e_min", "xg90", "xa90", "dc_hit"]]
    return info.set_index("element").join(t, how="inner").join(feats).reset_index()


def save(proj, info, nxt, season=data.CURRENT_SEASON):
    path = ROOT / "predictions" / season / f"gw{nxt}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out = proj[proj["gw"] == nxt][["element", "gw", "xp", "xp_raw", "p_play", "p60", "e_min"]].copy()
    out["made_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    out.round(4).to_csv(path, index=False)
    return path


if __name__ == "__main__":
    proj, info, nxt, gws, boot, fx = projections()
    w = wide(proj, info, gws)
    if "--save" in sys.argv:
        print("saved", save(proj, info, nxt))
    pd.set_option("display.width", 200)
    for pos in ["GK", "DEF", "MID", "FWD"]:
        top = w[w["pos"] == pos].sort_values("xp_decayed", ascending=False).head(8)
        print(f"\n{pos} (next GW{nxt}, 6-GW total)")
        print(top[["name", "team", "price", "own", f"gw{nxt}", "xp_total", "p60", "status"]].round(2).to_string(index=False))
