"""Decision backtests with the agent's own optimiser (fpl/solver.py).

same_state  Every gameweek, the base model and a candidate face the identical squad, bank and
            free transfers (the state the base agent reached). Each picks transfers, XI and
            captain; we score what its plan scored over the next 3 gameweeks (its own XI and
            captain each week, auto-subs by real minutes, minus hits). Removes path dependence.
replay      Full season replay per model from a £100.0m GW6 squad. Realistic but noisy:
            two squads drift apart and luck compounds (season SD around 100 points).

Shared rules: 6-GW horizon, decay 0.9, Vuk's hit rule (a -4 only if the projected 3-GW gain
after the hit is 6+), free transfers roll to 5, selling price = purchase + half the profit
rounded down to £0.1m, no chips.
"""
import numpy as np
import pandas as pd

from fpl import backtest, solver

from .panel import OUT_DIR

backtest.PREV.update({"2023-24": "2022-23", "2024-25": "2023-24"})
HORIZON = 6
SCORE_GWS = 3
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}


def load_preds(model, res=None):
    res = res if res is not None else pd.read_parquet(OUT_DIR / "walk_forward.parquet")
    col = "xp" if model == "base" else "pred_" + model
    return res[["season", "origin", "element", "gw", col]].rename(columns={col: "xp"})


def sell_price(buy, now):
    return buy + np.floor((now - buy) / 2 * 10 + 1e-9) / 10 if now > buy else now


def autosub_points(week, pos, mins, pts):
    """FPL scoring of one gameweek's XI: auto-subs in bench order keeping a legal formation,
    vice-captain doubled if the captain did not play."""
    xi, bench, cap, vice = list(week["xi"]), list(week["bench"]), week["captain"], week["vice"]
    played = lambda e: mins.get(e, 0) > 0
    final = list(xi)
    for e in [e for e in xi if not played(e)]:
        for b in bench:
            if b in final or not played(b) or (pos[e] == "GK") != (pos[b] == "GK"):
                continue
            trial = [x for x in final if x != e] + [b]
            counts = {p: sum(pos[x] == p for x in trial) for p in XI_MIN}
            if all(counts[p] >= XI_MIN[p] for p in XI_MIN) and counts["GK"] == 1:
                final = trial
                break
    total = sum(pts.get(e, 0) for e in final)
    if played(cap):
        total += pts.get(cap, 0)
    elif vice in final and played(vice):
        total += pts.get(vice, 0)
    return total


def _state_info(s, g):
    latest = s.df[s.df["gw"] < g].sort_values("gw").groupby("element").tail(1).set_index("element")
    info = pd.DataFrame({"element": latest.index, "pos": latest["pos"].values, "team": latest["team"].values,
                         "price": latest["value"].values / 10})
    return info, (latest["value"] / 10).to_dict()


def _choose(proj, info, sell, bank, ft, gws):
    plans = solver.compare_transfers(proj, info, sell, bank, ft, gws, max_extra=1)
    ok = [p for p in plans if p["n_transfers"] <= ft or (p["gain_3gw"] is not None and p["gain_3gw"] >= 6)]
    return max(ok, key=lambda p: p["net"])


def _score(plan, actual, pos, weeks):
    total = -plan["hit_cost"]
    for wk in plan["weeks"][:weeks]:
        a = actual[actual["gw"] == wk["gw"]].set_index("element")
        total += autosub_points(wk, pos, a["minutes"].to_dict(), a["points"].to_dict())
    return total


def _advance(plan, squad, info, ft):
    t = plan["n_transfers"]
    ft = min(5, ft - t + 1) if t <= ft else 1
    price = info.set_index("element")["price"]
    return {e: (squad[e] if e in squad else float(price[e])) for e in plan["squad"]}, plan["bank_after"], ft


def same_state(season, model, res=None, log=print):
    s = backtest.Season(season)
    pb, pm = load_preds("base", res), load_preds(model, res)
    pb, pm = pb[pb["season"] == season], pm[pm["season"] == season]
    actual = s.df.groupby(["element", "gw"]).agg(points=("points", "sum"), minutes=("minutes", "sum")).reset_index()
    squad, bank, ft, rows = {}, 0.0, 1, []
    for g in sorted(pb["origin"].unique()):
        gws = sorted(x for x in set(pb.loc[pb["origin"] == g, "gw"]) if x < g + HORIZON)
        info, now = _state_info(s, g)
        pos = info.set_index("element")["pos"].to_dict()
        if not squad:
            plan = solver.solve(pb[pb["origin"] == g][["element", "gw", "xp"]], info, {}, 0.0, gws, 0,
                                unlimited=True, budget_total=100.0)
            squad, bank = {e: float(info.set_index("element").at[e, "price"]) for e in plan["squad"]}, plan["bank_after"]
            continue
        sell = {e: round(sell_price(b, now.get(e, b)), 1) for e, b in squad.items()}
        plans = {name: _choose(pr[pr["origin"] == g][["element", "gw", "xp"]], info, sell, bank, ft, gws)
                 for name, pr in (("base", pb), ("model", pm))}
        rows.append({"season": season, "gw": g, "base": _score(plans["base"], actual, pos, SCORE_GWS),
                     "model": _score(plans["model"], actual, pos, SCORE_GWS),
                     "same_captain": plans["base"]["weeks"][0]["captain"] == plans["model"]["weeks"][0]["captain"]})
        squad, bank, ft = _advance(plans["base"], squad, info, ft)
    log(f"same-state {season} {model} done")
    return pd.DataFrame(rows)


def replay(season, model, res=None, log=print):
    s = backtest.Season(season)
    pr = load_preds(model, res)
    pr = pr[pr["season"] == season]
    actual = s.df.groupby(["element", "gw"]).agg(points=("points", "sum"), minutes=("minutes", "sum")).reset_index()
    squad, bank, ft, rows = {}, 0.0, 1, []
    for g in sorted(pr["origin"].unique()):
        gws = sorted(x for x in set(pr.loc[pr["origin"] == g, "gw"]) if x < g + HORIZON)
        info, now = _state_info(s, g)
        pos = info.set_index("element")["pos"].to_dict()
        proj = pr[pr["origin"] == g][["element", "gw", "xp"]]
        if not squad:
            plan = solver.solve(proj, info, {}, 0.0, gws, 0, unlimited=True, budget_total=100.0)
            plan.update({"n_transfers": 0, "hit_cost": 0})
            squad = {}
        else:
            plan = _choose(proj, info, {e: round(sell_price(b, now.get(e, b)), 1) for e, b in squad.items()},
                           bank, ft, gws)
        rows.append({"season": season, "gw": g, "points": _score(plan, actual, pos, 1), "hits": plan["hit_cost"]})
        squad, bank, ft = _advance(plan, squad, info, ft) if squad else (
            {e: float(info.set_index("element").at[e, "price"]) for e in plan["squad"]}, plan["bank_after"], 1)
    log(f"replay {season} {model} done")
    return pd.DataFrame(rows)
