"""Tests for the rank-aware objective (fpl/rank.py) and its use in the optimiser."""
import unittest

import numpy as np
import pandas as pd

from fpl import rank, solver
from tests.test_gate_solver import market

VP = {"a": 3.3, "b": 0.0}


class Formulas(unittest.TestCase):
    def test_kappa_sign_follows_the_gap(self):
        self.assertGreater(rank.kappa(120, 27, 100), 0)      # behind: spread is worth points
        self.assertLess(rank.kappa(-50, 27, 100), 0)         # ahead: spread costs points
        self.assertEqual(rank.kappa(None, 27, 100), 0.0)     # no field data: pure xP
        self.assertAlmostEqual(rank.kappa(120, 27, 100), 120 / (2 * 27 * 100))

    def test_kappa_is_capped(self):
        self.assertEqual(rank.kappa(500, 5, 10), rank.KAPPA_CAP)
        self.assertEqual(rank.kappa(-500, 5, 10), -rank.KAPPA_CAP)

    def test_non_players_earn_nothing(self):
        self.assertEqual(float(rank.variance(0.0, VP)), 0.0)
        b = rank.bonus_table(pd.DataFrame({"element": [1], "gw": [12], "xp": [0.0]}), {1: 0.0}, 0.03, VP)
        self.assertEqual(float(b["start"].iloc[0]), 0.0)

    def test_bonus_direction_by_ownership(self):
        proj = pd.DataFrame({"element": [1, 2], "gw": [12, 12], "xp": [4.0, 4.0]})
        b = rank.bonus_table(proj, {1: 0.9, 2: 0.05}, 0.03, VP).set_index("element")
        self.assertLess(b.at[1, "start"], 0)          # template player: owning him narrows the spread
        self.assertGreater(b.at[2, "start"], 0)       # differential: widens it
        # Captain increment (3 - 2e) * sigma^2 * kappa
        self.assertAlmostEqual(b.at[2, "cap"], 0.03 * 3.3 * 4.0 * (3 - 0.1))

    def test_weekly_spread_by_hand(self):
        # You start A (xP 4) and captain him; the field: A at EO 1.5, B (xP 2) at EO 0.5.
        s = rank.weekly_spread(xi=[1], captain=1, xp_by_element={1: 4.0, 2: 2.0},
                               eo_by_element={1: 1.5, 2: 0.5}, vp=VP)
        expected = (2 - 1.5) ** 2 * 3.3 * 4.0 + (0 - 0.5) ** 2 * 3.3 * 2.0
        self.assertAlmostEqual(s, expected)


class SolverWithBonus(unittest.TestCase):
    def setUp(self):
        self.info, self.proj, self.gws = market()
        by_pos = {p: list(self.info[self.info["pos"] == p]["element"]) for p in solver.QUOTA}
        squad_ids = by_pos["GK"][:2] + by_pos["DEF"][:5] + by_pos["MID"][:5] + by_pos["FWD"][:3]
        self.squad = {e: float(self.info.set_index("element").at[e, "price"]) for e in squad_ids}
        # Two outside midfielders with identical xP and price: 23 (template) and 24 (differential).
        self.proj.loc[self.proj["element"].isin([23, 24]), "xp"] = 5.0
        self.info.loc[self.info["element"].isin([23, 24]), "price"] = 5.0
        self.info.loc[self.info["element"] == 24, "team"] = "T3"   # avoid club-limit asymmetry
        self.eo = {23: 0.9, 24: 0.05}

    def best_in(self, k):
        bonus = rank.bonus_table(self.proj, self.eo, k, VP) if k is not None else None
        plan = solver.solve(self.proj, self.info, self.squad, 10.0, self.gws, 1,
                            forbid_in=[e for e in range(25, 31)], bonus=bonus)
        return plan["transfers_in"][0], plan

    def test_behind_prefers_the_differential(self):
        e, plan = self.best_in(0.03)
        self.assertEqual(e, 24)
        self.assertGreater(plan["rank_bonus"], 0)

    def test_ahead_prefers_the_template(self):
        e, _ = self.best_in(-0.03)
        self.assertEqual(e, 23)

    def test_reported_points_stay_pure_xp(self):
        _, with_bonus = self.best_in(0.03)
        _, without = self.best_in(None)
        self.assertEqual(with_bonus["points_by_gw"], without["points_by_gw"])


if __name__ == "__main__":
    unittest.main()
