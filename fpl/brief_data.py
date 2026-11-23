"""Everything the brief needs from the data, in one run.

  python -m fpl.brief_data            # writes brief_data.json and prints a compact summary
  python -m fpl.brief_data --save     # also logs this week's predictions for the review loop

Covers: squad rebuild, projections (6 gameweeks, and to GW19 for chip planning
in the first half), transfer plans for 0..FT+1 transfers, captain table,
chip values by gameweek, fixture ratings, and the data side of the edge scan.
The brief adds news, odds, top-10k ownership and judgement on top.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import live, model, solver
from . import squad as squad_mod

ROOT = Path(__file__).resolve().parent.parent
DATA_URL = "https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data"


def fixture_ratings(proj, info, gws, teams):
    """Per team, over the next gameweeks: attack ease (fixture attack factor) and clean-sheet odds."""
    p = proj.merge(info[["element", "team"]], on="element")
    t = p.groupby(["team", "gw"]).agg(att=("att_factor", "mean"), cs=("cs_prob", "mean"),
                                      opp=("opp", "first"), home=("home", "first")).reset_index()
    out = []
    for team, grp in t.groupby("team"):
        grp = grp.set_index("gw").reindex(gws)
        runs = [f"{teams.get(o[0], '?')}{'(H)' if h[0] else '(A)'}" if isinstance(o, list) and o else "BLANK"
                for o, h in zip(grp["opp"], grp["home"])]
        out.append({"team": team, "attack_ease": round(grp["att"].mean(), 3),
                    "cs_prob_avg": round(grp["cs"].mean(), 3), "fixtures": " ".join(runs)})
    return pd.DataFrame(out).sort_values("attack_ease", ascending=False)


def edges(w, cur, nxt):
    """Data-side edge scan. The brief checks each against news, odds and top-10k ownership."""
    flags = []
    w = w.copy()
    w["rank_pos"] = w.groupby("pos")["xp_decayed"].rank(ascending=False)
    nailed = w["p60"] >= 0.75
    # 1. Under-owned output
    for r in w[nailed & (w["own"] < 10) & (w["rank_pos"] <= 15)].sort_values("xp_decayed", ascending=False).itertuples():
        flags.append({"type": "under-owned output", "name": r.name, "team": r.team, "pos": r.pos, "price": r.price,
                      "own": r.own, "xp_6gw": round(r.xp_total, 1), "rank_in_pos": int(r.rank_pos)})
    # 3. Penalty takers at low ownership
    for r in w[(w["pens"] == 1) & (w["own"] < 10) & nailed].itertuples():
        flags.append({"type": "first-choice penalty taker, low owned", "name": r.name, "team": r.team,
                      "price": r.price, "own": r.own, "xp_6gw": round(r.xp_total, 1)})
    # 4. Finishing luck (season goals vs xG)
    season = cur.groupby("element").agg(goals=("goals", "sum"), xg=("xg", "sum"), mins=("minutes", "sum"))
    season["diff"] = season["goals"] - season["xg"]
    w = w.join(season, on="element")
    for r in w[nailed & (w["diff"] <= -2) & (w["xg"] >= 3)].itertuples():
        flags.append({"type": "unlucky finisher (buy low)", "name": r.name, "team": r.team, "price": r.price,
                      "own": r.own, "goals": int(r.goals), "xg": round(r.xg, 1)})
    for r in w[(w["own"] >= 20) & (w["diff"] >= 3)].itertuples():
        flags.append({"type": "overperforming template player (sell-high risk)", "name": r.name, "team": r.team,
                      "own": r.own, "goals": int(r.goals), "xg": round(r.xg, 1)})
    # 6. Defensive value: DefCon machines at low ownership
    for r in w[nailed & w["pos"].isin(["DEF", "MID"]) & (w["dc_hit"] >= 0.5) & (w["own"] < 10)].sort_values(
            "xp_decayed", ascending=False).head(5).itertuples():
        flags.append({"type": "DefCon value, low owned", "name": r.name, "team": r.team, "pos": r.pos,
                      "price": r.price, "own": r.own, "dc_hit": round(r.dc_hit, 2), "xp_6gw": round(r.xp_total, 1)})
    # 7. Minutes regained: 60+ in each of the last 2 GWs after mostly not before
    recent = cur[cur["gw"] >= nxt - 6].pivot_table(index="element", columns="gw", values="minutes", aggfunc="sum")
    if recent.shape[1] >= 4:
        last2 = (recent.iloc[:, -2:] >= 60).all(axis=1)
        before = (recent.iloc[:, :-2] >= 60).mean(axis=1) < 0.5
        for e in recent.index[last2 & before]:
            r = w[w["element"] == e]
            if len(r) and r["rank_pos"].iloc[0] <= 30:
                r = r.iloc[0]
                flags.append({"type": "minutes regained", "name": r["name"], "team": r["team"], "price": r["price"],
                              "own": r["own"], "xp_6gw": round(r["xp_total"], 1)})
    # 8. Template sell signals: highly owned, outside the top 25 in position over 6 GWs
    for r in w[(w["own"] >= 25) & (w["rank_pos"] > 25)].itertuples():
        flags.append({"type": "template player with weak 6-GW outlook", "name": r.name, "team": r.team,
                      "own": r.own, "xp_6gw": round(r.xp_total, 1), "rank_in_pos": int(r.rank_pos)})
    return flags


def main(save=False):
    data = squad_mod.load(DATA_URL)
    sq = squad_mod.rebuild(data)
    proj, info, nxt, gws, boot, fixtures = live.projections(horizon=6)
    chip_horizon = list(range(nxt, 20)) if nxt <= 19 else gws
    long_proj = live.projections(horizon=len(chip_horizon))[0] if len(chip_horizon) > len(gws) else proj
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}
    names = info.set_index("element")["name"] + " (" + info.set_index("element")["team"] + ")"
    squad = {p["id"]: p["sell"] / 10 for p in sq["players"]}
    bank, ft = sq["bank"] / 10, sq["free_transfers_estimate"] or 1

    plans = solver.compare_transfers(proj, info, squad, bank, ft, gws)
    chips = solver.chip_values(long_proj, info, squad, bank, chip_horizon)
    w = live.wide(proj, info, gws)
    cur = __import__("fpl.data", fromlist=["load_current_season"]).load_current_season()[0]

    owned = set(info.set_index("element").loc[list(squad), "name"])
    cap_table = w[w["element"].isin(squad)].sort_values(f"gw{nxt}", ascending=False).head(5)
    out = {
        "gameweek": nxt, "gws": gws,
        "deadline_utc": next(e["deadline_time"] for e in boot["events"] if e["id"] == nxt),
        "data_as_of": data["meta"]["fetched_at"],
        "squad": {**{k: sq[k] for k in ["team_name", "overall_rank", "bank", "free_transfers_estimate",
                                        "chips_left", "problems", "notes"]},
                  "players": [{"name": names[p["id"]], "pos": p["pos"], "sell": p["sell"] / 10,
                               "status": p["status"], "news": p["news"],
                               **{f"gw{g}": round(float(w.loc[w["element"] == p["id"], f"gw{g}"].sum()), 2) for g in gws}}
                              for p in sq["players"]]},
        "plans": [{"n_transfers": p["n_transfers"], "hit_cost": p["hit_cost"],
                   "out": [names[e] for e in p["transfers_out"]], "in": [names[e] for e in p["transfers_in"]],
                   "bank_after": p["bank_after"], "xi_points_by_gw": p["points_by_gw"],
                   "gain_1gw": p["gain_1gw"], "gain_3gw_after_hits": p["gain_3gw"], "net_score": p["net"],
                   "captain": names[p["weeks"][0]["captain"]], "vice": names[p["weeks"][0]["vice"]],
                   "bench_order": [names[e] for e in p["weeks"][0]["bench"]]} for p in plans],
        "captain_table": cap_table[["name", "team", "own", f"gw{nxt}", "xp_total"]].round(2).to_dict("records"),
        "chip_values": chips.assign(captain_xp=chips["captain_xp"]).round(2).to_dict("records"),
        "fixture_ratings": fixture_ratings(proj, info, gws, teams).to_dict("records"),
        "top_by_position": {pos: w[w["pos"] == pos].sort_values("xp_decayed", ascending=False).head(10)[
            ["name", "team", "price", "own", f"gw{nxt}", "xp_total", "p60", "status"]].round(2).to_dict("records")
            for pos in ["GK", "DEF", "MID", "FWD"]},
        "edges": [{**f, "owned": f["name"] in owned} for f in edges(w, cur, nxt)],
        "model_note": "xP calibrated with ownership; backtest: rank corr ~0.50 (1 GW), ~0.56 (6 GW); "
                      "low-owned top picks delivered ~89-92% of raw projection (see backtest/REPORT.md).",
    }
    (ROOT / "brief_data.json").write_text(json.dumps(out, indent=1, default=str))
    if save:
        print("predictions saved:", live.save(proj, info, nxt))
    return out


if __name__ == "__main__":
    o = main(save="--save" in sys.argv)
    print(f"GW{o['gameweek']} | deadline {o['deadline_utc']} UTC | data {o['data_as_of']}")
    s = o["squad"]
    print(f"Rank {s['overall_rank']:,} | bank £{s['bank'] / 10:.1f}m | FT est {s['free_transfers_estimate']} | "
          f"chips {[c['name'] for c in s['chips_left']]}")
    for p in o["plans"]:
        print(f"T={p['n_transfers']} hit={p['hit_cost']} out={p['out']} in={p['in']} bank={p['bank_after']} "
              f"GW+1 gain {p['gain_1gw']} | 3GW gain after hits {p['gain_3gw_after_hits']} | C {p['captain']}")
    print("Chips:", pd.DataFrame(o["chip_values"]).to_string(index=False))
    print(f"Edges: {len(o['edges'])} flags -> brief_data.json")
