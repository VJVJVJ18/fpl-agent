"""Everything the brief needs from the data, in one run.

  python -m fpl.brief_data            # writes brief_data.json and prints a compact summary
  python -m fpl.brief_data --save     # also logs this week's predictions for the review loop
  python -m fpl.brief_data --made="Tarkowski (EVE)>Van Hecke (TOT)"   # moves Vuk made that FPL hides until the deadline

Covers: squad rebuild, projections (6 gameweeks, and to GW19 for chip planning
in the first half), transfer plans for 0..FT+1 transfers, captain table,
chip values by gameweek, fixture ratings, and the data side of the edge scan.

Two sets of plans:
  rank_plans  rank-aware (fpl/rank.py): expected points plus the value of spread against
              the top 10k, sized from the points gap. This is Option A by default.
  plans       expected points only. Option B, the reference for what deviating costs.
The brief adds news, odds and judgement on top.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import live, model, rank, solver
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


def _best(plans):
    """The plan the brief starts from: best net score among plans that pass the hit rule."""
    ok = [p for p in plans if p["hit_cost"] == 0 or p["gain_3gw"] >= 6]
    return max(ok or plans, key=lambda p: p["net"])


def rank_setup(proj, info, squad, bank, ft, gws, nxt, entry, base_plan, iterations=5):
    """Rank-aware plans with kappa settled by damped fixed-point iteration on the weekly spread
    (deviating widens the spread, which lowers kappa; averaging stops it oscillating)."""
    field = rank.load_field()
    if not field:
        return None, None
    eo = dict(zip(field["eo"]["element"], field["eo"]["eo"]))
    own = dict(zip(field["eo"]["element"], field["eo"]["own"]))
    T, N = rank.gap(field, entry), rank.gws_left(nxt)
    xpn = proj[proj["gw"] == nxt].groupby("element")["xp"].sum().to_dict()
    wk = base_plan["weeks"][0]
    spread = rank.weekly_spread(wk["xi"], wk["captain"], xpn, eo)
    k = rank.kappa(T, N, spread)
    history = [{"kappa": round(k, 4), "weekly_sd": round(spread ** 0.5, 2), "from": "points-only plan"}]
    plans = None
    for _ in range(iterations):
        bonus = rank.bonus_table(proj, eo, k)
        plans = solver.compare_transfers(proj, info, squad, bank, ft, gws, bonus=bonus)
        best = _best(plans)
        spread = rank.weekly_spread(best["weeks"][0]["xi"], best["weeks"][0]["captain"], xpn, eo)
        k_new = rank.kappa(T, N, spread)
        history.append({"kappa": round(k_new, 4), "weekly_sd": round(spread ** 0.5, 2), "from": "rank plan"})
        if abs(k_new - k) <= 0.1 * abs(k) + 1e-4:
            break
        k = 0.5 * (k + k_new)
    info_out = {
        "target_rank": field["target"]["rank"] if field.get("target") else None,
        "target_total": field["target"]["total"] if field.get("target") else None,
        "our_total": entry.get("summary_overall_points"), "gap": T, "gws_left": N,
        "field_sample_gw": field["event"], "field_teams": field["teams"], "field_chips": field["chips"],
        "kappa": round(k, 4), "iterations": history,
        **{f"band_{key}": v for key, v in rank.bands(k).items()},
    }
    return {"plans": plans, "eo": eo, "own": own, "bonus": rank.bonus_table(proj, eo, k)}, info_out


def deviation_candidates(w, xi, own, bonus, budget_extra, gws, threshold=0.6, diff_max=0.3, top=2):
    """For each template starter, low-owned alternatives in the same position whose
    rank-adjusted 6-GW value (xP + rank bonus for starting) beats his."""
    b6 = bonus.groupby("element")["start"].sum()
    w = w.assign(own10k=w["element"].map(own).fillna(0.0), bonus6=w["element"].map(b6).fillna(0.0))
    w["value6"] = w["xp_total"] + w["bonus6"]
    out = []
    for e in xi:
        row = w[w["element"] == e]
        if row.empty or row["own10k"].iloc[0] < threshold:
            continue
        r = row.iloc[0]
        alts = w[(w["pos"] == r["pos"]) & (w["own10k"] < diff_max) & (w["p60"] >= 0.75) &
                 (w["price"] <= r["price"] + budget_extra) & (w["value6"] > r["value6"]) & (w["element"] != e)]
        for a in alts.sort_values("value6", ascending=False).head(top).itertuples():
            out.append({"template": f"{r['name']} ({r['team']})", "template_own10k": round(r["own10k"], 3),
                        "template_xp6": round(r["xp_total"], 2), "alt": f"{a.name} ({a.team})", "alt_price": a.price,
                        "alt_own10k": round(a.own10k, 3), "alt_xp6": round(a.xp_total, 2),
                        "xp_cost": round(r["xp_total"] - a.xp_total, 2),
                        "rank_value_gain": round(a.value6 - r["value6"], 2)})
    return out


def apply_made(sq, info, made):
    """Transfers Vuk has made that FPL won't show until the deadline: ["Out (TEAM)>In (TEAM)", ...].
    Buys at the current price, spends a free transfer each."""
    label = info["name"] + " (" + info["team"] + ")"
    by_label = dict(zip(label, info["element"]))
    price = dict(zip(info["element"], info["price"]))
    for move in made:
        out_l, in_l = [x.strip() for x in move.split(">")]
        out_e, in_e = by_label[out_l], by_label[in_l]
        player = next(p for p in sq["players"] if p["id"] == out_e)
        sq["bank"] += player["sell"] - round(price[in_e] * 10)
        row = info.set_index("element").loc[in_e]
        player.update({"id": in_e, "sell": round(price[in_e] * 10), "name": row["name"], "team": row["team"], "pos": info.set_index("element").at[in_e, "pos"],
                       "status": info.set_index("element").at[in_e, "status"], "news": info.set_index("element").at[in_e, "news"]})
        sq["free_transfers_estimate"] = max(0, (sq["free_transfers_estimate"] or 1) - 1)
        sq.setdefault("notes", []).append(f"assumed made: {move}")
    return sq


def main(save=False, made=()):
    data = squad_mod.load(DATA_URL)
    sq = squad_mod.rebuild(data)
    proj, info, nxt, gws, boot, fixtures = live.projections(horizon=6)
    if made:
        sq = apply_made(sq, info, made)
    chip_horizon = list(range(nxt, 20)) if nxt <= 19 else gws
    long_proj = live.projections(horizon=len(chip_horizon))[0] if len(chip_horizon) > len(gws) else proj
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}
    names = info.set_index("element")["name"] + " (" + info.set_index("element")["team"] + ")"
    squad = {p["id"]: p["sell"] / 10 for p in sq["players"]}
    bank, ft = sq["bank"] / 10, sq["free_transfers_estimate"] if made else (sq["free_transfers_estimate"] or 1)

    plans = solver.compare_transfers(proj, info, squad, bank, ft, gws)
    chips = solver.chip_values(long_proj, info, squad, bank, chip_horizon)
    w = live.wide(proj, info, gws)
    cur = __import__("fpl.data", fromlist=["load_current_season"]).load_current_season()[0]
    rk, rank_info = rank_setup(proj, info, squad, bank, ft, gws, nxt, data["entry"], _best(plans))
    own10k = rk["own"] if rk else {}
    eo10k = rk["eo"] if rk else {}

    def plan_rows(ps):
        return [{"n_transfers": p["n_transfers"], "hit_cost": p["hit_cost"],
                 "out": [names[e] for e in p["transfers_out"]], "in": [names[e] for e in p["transfers_in"]],
                 "bank_after": p["bank_after"], "xi_points_by_gw": p["points_by_gw"],
                 "gain_1gw": p["gain_1gw"], "gain_3gw_after_hits": p["gain_3gw"], "net_score": p["net"],
                 "rank_bonus": p.get("rank_bonus", 0.0),
                 "captain": names[p["weeks"][0]["captain"]], "vice": names[p["weeks"][0]["vice"]],
                 "bench_order": [names[e] for e in p["weeks"][0]["bench"]],
                 "template_count_xi": rank.template_count(p["weeks"][0]["xi"], own10k) if own10k else None,
                 "xi_top10k_own": {names[e]: round(own10k.get(e, 0.0), 3) for e in p["weeks"][0]["xi"]} if own10k else None}
                for p in ps]

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
        "rank": rank_info,
        "rank_plans": plan_rows(rk["plans"]) if rk else None,
        "plans": plan_rows(plans),
        "deviation_candidates": deviation_candidates(w, _best(rk["plans"] if rk else plans)["weeks"][0]["xi"],
                                                     own10k, rk["bonus"], bank, gws) if rk else [],
        "captain_table": cap_table.assign(
            top10k_eo=cap_table["element"].map(eo10k).round(3) if eo10k else None,
            # what the armband adds on the rank objective: his xP again plus the captain bonus
            rank_cap_value=(cap_table[f"gw{nxt}"] + cap_table["element"].map(
                rk["bonus"][rk["bonus"]["gw"] == nxt].set_index("element")["cap"]).fillna(0)).round(2)
            if rk else None)[["name", "team", "own", "top10k_eo", f"gw{nxt}", "rank_cap_value", "xp_total"]
                             ].round(2).to_dict("records"),
        "chip_values": chips.assign(captain_xp=chips["captain_xp"]).round(2).to_dict("records"),
        "fixture_ratings": fixture_ratings(proj, info, gws, teams).to_dict("records"),
        "top_by_position": {pos: w[w["pos"] == pos].sort_values("xp_decayed", ascending=False).head(10)[
            ["name", "team", "price", "own", f"gw{nxt}", "xp_total", "p60", "status"]].assign(
            own10k=lambda d: [round(own10k.get(int(e), 0.0), 3) for e in w.loc[d.index, "element"]] if own10k else None
            ).round(2).to_dict("records")
            for pos in ["GK", "DEF", "MID", "FWD"]},
        "edges": [{**f, "owned": f["name"] in owned,
                   "own10k": round(own10k.get(int(w.loc[(w["name"] == f["name"]) & (w["team"] == f["team"]), "element"].iloc[0]), 0.0), 3)
                   if own10k and len(w[(w["name"] == f["name"]) & (w["team"] == f["team"])]) else None}
                  for f in edges(w, cur, nxt)],
        "model_note": "xP calibrated with ownership; backtest: rank corr ~0.50 (1 GW), ~0.56 (6 GW); "
                      "low-owned top picks delivered ~89-92% of raw projection (see backtest/REPORT.md).",
    }
    (ROOT / "brief_data.json").write_text(json.dumps(out, indent=1, default=str))
    if save:
        print("predictions saved:", live.save(proj, info, nxt))
    return out


if __name__ == "__main__":
    made = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--made=")]
    o = main(save="--save" in sys.argv, made=made)
    print(f"GW{o['gameweek']} | deadline {o['deadline_utc']} UTC | data {o['data_as_of']}")
    s = o["squad"]
    print(f"Rank {s['overall_rank']:,} | bank £{s['bank'] / 10:.1f}m | FT est {s['free_transfers_estimate']} | "
          f"chips {[c['name'] for c in s['chips_left']]}")
    r = o["rank"]
    if r:
        print(f"Rank objective: {r['gap']:+.0f} pts to rank {r['target_rank']:,} ({r['target_total']}), {r['gws_left']} GWs left, "
              f"field sample GW{r['field_sample_gw']} ({r['field_teams']} teams) | kappa {r['kappa']} | "
              f"captain band ~{r['band_captain_pts']} pts, slot band ~{r['band_slot_6gw_pts']} pts over 6 GWs")
    for label, ps in [("RANK", o["rank_plans"] or []), ("POINTS", o["plans"])]:
        for p in ps:
            print(f"{label} T={p['n_transfers']} hit={p['hit_cost']} out={p['out']} in={p['in']} bank={p['bank_after']} "
                  f"GW+1 gain {p['gain_1gw']} | 3GW gain after hits {p['gain_3gw_after_hits']} | C {p['captain']} | "
                  f"template XI {p['template_count_xi']}")
    for d in o["deviation_candidates"]:
        print("DEVIATE?", d)
    print("Chips:", pd.DataFrame(o["chip_values"]).to_string(index=False))
    print(f"Edges: {len(o['edges'])} flags -> brief_data.json")
