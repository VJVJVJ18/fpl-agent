"""Rank-aware objective: how many expected points to trade for spread against the target rank.

The goal is a finishing rank (top 10k), not a points total. Write T for the points gap
to the manager at the target rank, M for the points expected to be gained on that field
over the remaining N gameweeks, and S^2 for the variance of (your total - their total).
With a normal approximation, the chance of finishing above the target is

    P = Phi((M - T) / S)

A decision that costs d expected points and adds v to the variance changes the z-score by

    dz = (-d + (T - M) * v / (2 S^2)) / S

so it is worth taking when d < kappa * v, with kappa = (T - M) / (2 S^2).

Behind the target (T > M), kappa > 0: spread is worth points, so low-owned picks gain.
Ahead of it, kappa < 0: spread costs points, so the template is preferred. As the gap
closes, kappa shrinks towards 0 and the plan converges on pure expected points.

Variance per player-gameweek against a field with effective ownership e (the field's
mean multiplier on him: 1 per starter, 2 per captain): if your multiplier is m, his
points enter the gap with weight (m - e), so the variance is (m - e)^2 * sigma^2.
  starting him (0 -> 1) adds (1 - 2e) * sigma^2
  captaining him (1 -> 2) adds a further (3 - 2e) * sigma^2
The optimiser adds kappa * sigma^2 * (1 - 2e) per starter and kappa * sigma^2 * (3 - 2e)
for the captain. sigma^2 comes from the backtest: Var(points) ~ a*xP + b*xP^2 (params.json),
so a player who will not play earns nothing for being low-owned.

Simplifications:
  - M = 0: no edge over the field is assumed (an edge would make kappa a little smaller).
  - Players are treated as independent (team-mates' points are correlated in reality,
    so stacks carry more spread than this counts).
  - S^2 = N * (weekly spread of your XI against the field), recomputed from the plan
    until it settles, so kappa falls as the team deviates.
  - kappa is capped at KAPPA_CAP either way, because the normal approximation is local.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import data

ROOT = Path(__file__).resolve().parent.parent
KAPPA_CAP = 0.05
DEFAULT_VARIANCE = {"a": 3.3, "b": -0.046}   # fitted on 2025/26 GW6-38 (backtest), Var = a*xP + b*xP^2
LAST_GW = 38


def variance_params():
    path = ROOT / "params.json"
    try:
        return json.loads(path.read_text()).get("variance") or DEFAULT_VARIANCE
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_VARIANCE


def variance(xp, vp=None):
    """Variance of a player's points in a gameweek given his projection."""
    vp = vp or variance_params()
    x = np.clip(np.asarray(xp, dtype=float), 0, None)
    return np.clip(vp["a"] * x + vp["b"] * x ** 2, 0, None)


def load_field(base=data.COLLECTOR):
    """Latest top-10k sample and the target line from the collector. None if not collected yet."""
    try:
        meta = data._json(base, "meta.json")
    except Exception:
        return None
    events = meta.get("top10k_events") or []
    if not events:
        return None
    sample = data._json(base, f"top10k/{events[-1]}.json")
    eo = pd.DataFrame([{"element": int(k), **v} for k, v in sample["players"].items()])
    return {"event": sample["event"], "teams": sample["teams"], "chips": sample.get("chips", {}),
            "eo": eo, "target": meta.get("target")}


def gap(field, entry):
    """Points behind the target rank (negative when ahead)."""
    if not field or not field.get("target"):
        return None
    return float(field["target"]["total"]) - float(entry["summary_overall_points"])


def weekly_spread(xi, captain, xp_by_element, eo_by_element, vp=None):
    """Variance of (your points - field points) for one gameweek, summed over every player
    who is in your XI or owned by the field."""
    players = set(xi) | set(eo_by_element)
    total = 0.0
    for e in players:
        m = (1 if e in xi else 0) + (1 if e == captain else 0)
        d = m - eo_by_element.get(e, 0.0)
        total += d * d * float(variance(xp_by_element.get(e, 0.0), vp))
    return total


def kappa(gap_points, gws_left, spread, cap=KAPPA_CAP):
    """Expected points worth giving up per unit of added variance."""
    if gap_points is None or gws_left <= 0 or spread <= 0:
        return 0.0
    k = gap_points / (2.0 * gws_left * spread)
    return float(np.clip(k, -cap, cap))


def bonus_table(proj, eo_by_element, k, vp=None):
    """Per (element, gw): the objective bonus for starting and for captaining him."""
    out = proj[["element", "gw", "xp"]].copy()
    e = out["element"].map(eo_by_element).fillna(0.0)
    s2 = variance(out["xp"], vp)
    out["start"] = k * s2 * (1 - 2 * e)
    out["cap"] = k * s2 * (3 - 2 * e)
    out["eo"] = e
    return out[["element", "gw", "start", "cap", "eo"]]


def bands(k, vp=None):
    """kappa in plain points, for two reference decisions:
    captain: armband from a 1.87-EO player to a 0.1-EO one, both projecting 5 points;
    slot: a 0.1-owned starter instead of a 0.9-owned one, both projecting 4 a week, for 6 GWs.
    Each figure is the most expected points worth giving up for that extra spread."""
    s5, s4 = float(variance(5.0, vp)), float(variance(4.0, vp))
    v_cap = (2 * 1.87 - 3) * s5 + (3 - 2 * 0.1) * s5
    v_slot = 6 * ((1 - 2 * 0.1) * s4 - (1 - 2 * 0.9) * s4)
    return {"captain_pts": round(k * v_cap, 2), "slot_6gw_pts": round(k * v_slot, 2)}


def gws_left(next_gw):
    return LAST_GW - next_gw + 1


def template_count(xi, own_by_element, threshold=0.6):
    """How many of the XI the field owns at or above the threshold (ownership, not EO)."""
    return sum(1 for e in xi if own_by_element.get(e, 0.0) >= threshold)
