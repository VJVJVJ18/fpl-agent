"""Transfer, lineup, captain and chip optimiser (mixed-integer program, HiGHS via scipy).

Given projections for the next gameweeks and the current squad, finds the squad
after this week's transfers that maximises discounted expected points:

  maximise  sum over gw of  d^k x [ xP(starting XI) + xP(captain) + bench_w x xP(bench) ]
            (+ optional rank bonus per starter and captain, see fpl/rank.py)
  subject to  15 players, 2/5/5/3, max 3 per club, budget at selling prices,
              exactly T transfers, valid XI each gameweek (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD),
              one designated starting goalkeeper who starts every gameweek he is available
              (no goalkeeper rotation, Vuk's rule; the backup only covers absences)

Usage (from the brief):
  from fpl import live, solver
  proj, info, nxt, gws, boot, fx = live.projections()
  plans = solver.compare_transfers(proj, info, squad, bank, ft)       # T = 0..ft+1
  chips = solver.chip_values(proj, info, squad, bank, gws)            # BB and FH value per GW
"""
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

QUOTA = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}
DECAY = 0.9          # weight of each later gameweek
BENCH_W = 0.1        # value of bench points (auto-sub cover)
FT_VALUE = 1.5       # assumed value of a free transfer carried to next week (not yet backtested)
CANDIDATES = 30      # best players per position considered, plus the current squad
AVAILABLE_XP = 0.3   # a keeper projecting less than this in a gameweek counts as unavailable


def _table(proj, gws):
    t = proj.pivot_table(index="element", columns="gw", values="xp", aggfunc="sum").reindex(columns=gws).fillna(0)
    return t


def solve(proj, info, squad, bank, gws, transfers, decay=DECAY, bench_w=BENCH_W,
          forbid_in=(), require_in=(), unlimited=False, budget_total=None, bonus=None):
    """Best squad after exactly `transfers` transfers (or any number if unlimited).

    squad: dict element -> selling price in £m. bank in £m. Prices in info["price"] (£m).
    budget_total: for a Free Hit, the money available (selling value + bank).
    bonus: optional DataFrame (element, gw, start, cap) from rank.bonus_table: objective
      credit for starting / captaining a player, on top of his xP. Reported points stay pure xP.
    Returns a dict with transfers, XI, captain and bench per gameweek, and points.
    """
    xp = _table(proj, gws)
    info = info.set_index("element")
    w = np.array([decay ** k for k in range(len(gws))])
    if bonus is not None:
        b_start = bonus.pivot_table(index="element", columns="gw", values="start", aggfunc="sum").reindex(columns=gws).fillna(0)
        b_cap = bonus.pivot_table(index="element", columns="gw", values="cap", aggfunc="sum").reindex(columns=gws).fillna(0)
    else:
        b_start = b_cap = pd.DataFrame(0.0, index=xp.index, columns=gws)
    score = ((xp.values + b_start.reindex(xp.index).fillna(0).values) * w).sum(axis=1)
    cand = set(squad)
    for pos in QUOTA:
        ids = [e for e in xp.index if info.at[e, "pos"] == pos] if len(xp) else []
        ranked = sorted(ids, key=lambda e: -score[xp.index.get_loc(e)])
        cand |= set(ranked[:CANDIDATES])
    cand |= set(require_in)
    cand = [e for e in cand if e in info.index and e not in set(forbid_in) - set(squad)]
    xp = xp.reindex(cand).fillna(0)
    bs = b_start.reindex(cand).fillna(0).values
    bc = b_cap.reindex(cand).fillna(0).values
    n, G = len(cand), len(gws)
    owned = np.array([e in squad for e in cand])
    price = np.array([squad[e] if e in squad else info.at[e, "price"] for e in cand])
    pos = np.array([info.at[e, "pos"] for e in cand])
    club = np.array([info.at[e, "team"] for e in cand])

    # Variable layout: x[i] | y[g, i] | c[g, i] | s[i] (designated starting goalkeeper)
    nv = n + 2 * G * n + n
    X = lambda i: i
    Y = lambda g, i: n + g * n + i
    C = lambda g, i: n + G * n + g * n + i
    S = lambda i: n + 2 * G * n + i
    cost = np.zeros(nv)
    for g in range(G):
        for i in range(n):
            v = w[g] * xp.values[i, g]
            cost[Y(g, i)] -= v * (1 - bench_w) + w[g] * bs[i, g]
            cost[C(g, i)] -= v + w[g] * bc[i, g]
            cost[X(i)] -= v * bench_w

    rows, lo, hi = [], [], []

    def add(coeffs, low, high):
        rows.append(coeffs)
        lo.append(low)
        hi.append(high)

    add({X(i): 1 for i in range(n)}, 15, 15)
    for p, q in QUOTA.items():
        add({X(i): 1 for i in range(n) if pos[i] == p}, q, q)
    for t in set(club):
        add({X(i): 1 for i in range(n) if club[i] == t}, 0, 3)
    money = budget_total if budget_total is not None else bank + sum(squad.values())
    add({X(i): price[i] for i in range(n)}, 0, money + 1e-6)
    if not unlimited:
        add({X(i): 1 for i in range(n) if not owned[i]}, transfers, transfers)
    for e in require_in:
        add({X(cand.index(e)): 1}, 1, 1)
    # No goalkeeper rotation: one designated starter, who must start whenever he is available.
    gks = [i for i in range(n) if pos[i] == "GK"]
    add({S(i): 1 for i in gks}, 1, 1)
    for i in range(n):
        if pos[i] != "GK":
            add({S(i): 1}, 0, 0)
            continue
        add({S(i): 1, X(i): -1}, -np.inf, 0)
        for g in range(G):
            if xp.values[i, g] > AVAILABLE_XP:
                add({Y(g, i): 1, S(i): -1}, 0, np.inf)
    for g in range(G):
        add({Y(g, i): 1 for i in range(n)}, 11, 11)
        add({C(g, i): 1 for i in range(n)}, 1, 1)
        for p in QUOTA:
            add({Y(g, i): 1 for i in range(n) if pos[i] == p}, XI_MIN[p], XI_MAX[p])
        for i in range(n):
            add({Y(g, i): 1, X(i): -1}, -np.inf, 0)
            add({C(g, i): 1, Y(g, i): -1}, -np.inf, 0)

    A = lil_matrix((len(rows), nv))
    for r, coeffs in enumerate(rows):
        for j, v in coeffs.items():
            A[r, j] = v
    res = milp(cost, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=np.ones(nv),
               bounds=Bounds(0, 1), options={"time_limit": 60})
    if res.x is None:
        return None
    x = res.x.round().astype(int)
    chosen = [cand[i] for i in range(n) if x[X(i)]]
    gk_start = next((cand[i] for i in range(n) if x[S(i)]), None)
    plan = {"transfers_out": [e for e in squad if e not in chosen], "starting_gk": gk_start,
            "transfers_in": [e for e in chosen if e not in squad],
            "squad": chosen, "weeks": []}
    spent = sum(price[i] for i in range(n) if x[X(i)])
    plan["bank_after"] = round(money - spent, 1)
    for g, gw in enumerate(gws):
        xi = [cand[i] for i in range(n) if x[Y(g, i)]]
        cap = next(cand[i] for i in range(n) if x[C(g, i)])
        bench = [e for e in chosen if e not in xi]
        gk_b = [e for e in bench if info.at[e, "pos"] == "GK"]
        out_b = sorted([e for e in bench if e not in gk_b], key=lambda e: -xp.at[e, gw])
        xi_pts = sum(xp.at[e, gw] for e in xi) + xp.at[cap, gw]
        plan["weeks"].append({"gw": gw, "xi": xi, "captain": cap,
                              "vice": max((e for e in xi if e != cap), key=lambda e: xp.at[e, gw]),
                              "bench": out_b + gk_b, "xi_points": round(xi_pts, 2),
                              "bench_points": round(sum(xp.at[e, gw] for e in bench), 2)})
    plan["objective"] = round(-res.fun, 2)
    plan["rank_bonus"] = round(sum(w[g] * (bs[i, g] * x[Y(g, i)] + bc[i, g] * x[C(g, i)])
                                   for g in range(G) for i in range(n)), 2)
    plan["points_by_gw"] = [wk["xi_points"] for wk in plan["weeks"]]
    return plan


def compare_transfers(proj, info, squad, bank, ft, gws, max_extra=1, **kw):
    """Plans for 0 .. ft+max_extra transfers, with hits and the value of rolling."""
    out = []
    for t in range(0, ft + max_extra + 1):
        plan = solve(proj, info, squad, bank, gws, t, **kw)
        if plan is None:
            continue
        hits = max(0, t - ft) * 4
        next_ft = min(5, ft - t + 1) if t <= ft else 1
        plan.update({"n_transfers": t, "hit_cost": hits, "next_ft": next_ft,
                     "net": round(plan["objective"] - hits + FT_VALUE * next_ft, 2),
                     "gain_3gw": None})
        out.append(plan)
    if out:
        base = out[0]["points_by_gw"]
        for plan in out:
            plan["gain_3gw"] = round(sum(plan["points_by_gw"][:3]) - sum(base[:3]) - plan["hit_cost"], 2)
            plan["gain_1gw"] = round(plan["points_by_gw"][0] - base[0], 2)
    return out


def chip_values(proj, info, squad, bank, gws):
    """For each gameweek: Bench Boost value (bench xP with the current squad) and
    Free Hit value (best one-week XI within the squad's total value, minus own XI)."""
    stay = solve(proj, info, squad, bank, gws, 0)
    money = bank + sum(squad.values())
    rows = []
    for k, gw in enumerate(gws):
        fh = solve(proj, info, squad, bank, [gw], 0, unlimited=True, budget_total=money)
        own_xi = stay["weeks"][k]["xi_points"]
        rows.append({"gw": gw, "bench_boost": stay["weeks"][k]["bench_points"],
                     "free_hit_gain": round(fh["weeks"][0]["xi_points"] - own_xi, 2) if fh else None,
                     "captain_xp": round(proj[(proj["gw"] == gw) & (proj["element"] == stay["weeks"][k]["captain"])]["xp"].sum(), 2)})
    return pd.DataFrame(rows)
