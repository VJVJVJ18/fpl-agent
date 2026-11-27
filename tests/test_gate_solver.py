"""Tests for the daily gate's decisions and the optimiser's legality rules."""
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd

from fpl import gate, solver

NOW = datetime(2026, 11, 26, 7, 46, tzinfo=timezone.utc)


def boot(deadline_in_h, gw=12, finished_prev=True, checked_prev=True):
    dl = (NOW + timedelta(hours=deadline_in_h)).isoformat().replace("+00:00", "Z")
    prev = (NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    return {"events": [
        {"id": gw - 1, "deadline_time": prev, "finished": finished_prev, "data_checked": checked_prev},
        {"id": gw, "deadline_time": dl, "finished": False, "data_checked": False}]}


FRESH = {"fetched_at": (NOW - timedelta(hours=1)).isoformat()}


class Gate(unittest.TestCase):
    def test_skip_when_far(self):
        out = gate.decide(boot(82), FRESH, NOW, done_reviews={11}, briefed=set())
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("SKIP"))

    def test_brief_within_36h(self):
        out = gate.decide(boot(34.7), FRESH, NOW, done_reviews={11}, briefed=set())
        self.assertTrue(out[0].startswith("BRIEF GW12"))
        self.assertIn("first brief", out[0])

    def test_second_brief_is_update(self):
        out = gate.decide(boot(10.7), FRESH, NOW, done_reviews={11}, briefed={12})
        self.assertIn("update", out[0])

    def test_review_when_final_and_unreviewed(self):
        out = gate.decide(boot(82, gw=13), FRESH, NOW, done_reviews=set(), briefed=set())
        self.assertIn("REVIEW GW12", out)
        self.assertFalse(any(a.startswith("SKIP") for a in out))

    def test_no_review_before_data_final(self):
        out = gate.decide(boot(82, gw=13, checked_prev=False), FRESH, NOW, done_reviews=set(), briefed=set())
        self.assertNotIn("REVIEW GW12", out)

    def test_stale_data_flagged(self):
        old = {"fetched_at": (NOW - timedelta(hours=9)).isoformat()}
        out = gate.decide(boot(82), old, NOW, done_reviews={11}, briefed=set())
        self.assertTrue(out[0].startswith("STALE"))


def market():
    """30 players: 4 GK, 10 DEF, 10 MID, 6 FWD across 10 clubs; xP rises with id."""
    rows, pos_list = [], ["GK"] * 4 + ["DEF"] * 10 + ["MID"] * 10 + ["FWD"] * 6
    for i, p in enumerate(pos_list, start=1):
        rows.append({"element": i, "pos": p, "team": f"T{i % 10}", "price": 4.5 + (i % 5), "name": f"P{i}"})
    info = pd.DataFrame(rows)
    gws = [12, 13]
    proj = pd.DataFrame([{"element": i, "gw": g, "xp": 1 + i / 10} for i in info["element"] for g in gws])
    return info, proj, gws


class Solver(unittest.TestCase):
    def setUp(self):
        self.info, self.proj, self.gws = market()
        by_pos = {p: list(self.info[self.info["pos"] == p]["element"]) for p in solver.QUOTA}
        squad_ids = by_pos["GK"][:2] + by_pos["DEF"][:5] + by_pos["MID"][:5] + by_pos["FWD"][:3]
        self.squad = {e: float(self.info.set_index("element").at[e, "price"]) for e in squad_ids}

    def check_legal(self, plan, bank):
        info = self.info.set_index("element")
        self.assertEqual(len(plan["squad"]), 15)
        self.assertEqual(Counter(info.loc[plan["squad"], "pos"]), Counter(solver.QUOTA))
        self.assertLessEqual(max(Counter(info.loc[plan["squad"], "team"]).values()), 3)
        self.assertGreaterEqual(plan["bank_after"], -1e-6)
        for wk in plan["weeks"]:
            self.assertEqual(len(wk["xi"]), 11)
            c = Counter(info.loc[wk["xi"], "pos"])
            self.assertEqual(c["GK"], 1)
            self.assertGreaterEqual(c["DEF"], 3)
            self.assertGreaterEqual(c["FWD"], 1)
            self.assertIn(wk["captain"], wk["xi"])

    def test_exact_transfer_count_and_legality(self):
        for t in (0, 1, 2):
            plan = solver.solve(self.proj, self.info, self.squad, 5.0, self.gws, t)
            self.assertEqual(len(plan["transfers_in"]), t)
            self.check_legal(plan, 5.0)

    def test_budget_binds(self):
        plan = solver.solve(self.proj, self.info, self.squad, 0.0, self.gws, 1)
        self.check_legal(plan, 0.0)

    def test_no_goalkeeper_rotation(self):
        # Keepers 1 and 2 (both owned) alternate the better fixture: the plan must not rotate them.
        proj = self.proj.copy()
        proj.loc[(proj["element"] == 1) & (proj["gw"] == 12), "xp"] = 6.0
        proj.loc[(proj["element"] == 2) & (proj["gw"] == 13), "xp"] = 6.0
        plan = solver.solve(proj, self.info, self.squad, 0.0, self.gws, 0, forbid_in=[3, 4])
        starters = {next(e for e in wk["xi"] if e in (1, 2)) for wk in plan["weeks"]}
        self.assertEqual(len(starters), 1)
        self.assertEqual(plan["starting_gk"], starters.pop())

    def test_backup_keeper_covers_an_absence_only(self):
        proj = self.proj.copy()
        proj.loc[proj["element"] == 1, "xp"] = 5.0
        proj.loc[(proj["element"] == 1) & (proj["gw"] == 12), "xp"] = 0.0   # starter out for one week
        plan = solver.solve(proj, self.info, self.squad, 0.0, self.gws, 0, forbid_in=[3, 4])
        self.assertEqual(plan["starting_gk"], 1)
        self.assertIn(2, plan["weeks"][0]["xi"])
        self.assertIn(1, plan["weeks"][1]["xi"])

    def test_hit_accounting(self):
        plans = solver.compare_transfers(self.proj, self.info, self.squad, 5.0, ft=1, gws=self.gws)
        self.assertEqual([p["hit_cost"] for p in plans], [0, 0, 4])


if __name__ == "__main__":
    unittest.main()
