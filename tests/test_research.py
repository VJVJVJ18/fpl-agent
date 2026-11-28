"""Unit checks for the flow alarm, the shadow model's live gate and its feature origin.
Synthetic data only, so this runs anywhere without network or LightGBM."""
import unittest

import numpy as np
import pandas as pd

from fpl import brief_data, review, shadow


def boot_with_flow():
    els = []
    for i in range(1, 21):
        els.append({"id": i, "selected_by_percent": "10.0", "transfers_in_event": 1000,
                    "transfers_out_event": 1000 + (60000 if i == 1 else 0) + (3000 if i == 2 else 0)})
    return {"total_players": 1_000_000, "elements": els}


def wide_table():
    return pd.DataFrame({"element": range(1, 21), "name": [f"P{i}" for i in range(1, 21)], "team": "AAA",
                         "pos": "MID", "p60": 0.9, "status": "a", "own": 10.0,
                         "xp_decayed": np.linspace(30, 10, 20), "xp_total": np.linspace(30, 10, 20)})


class FlowAlarm(unittest.TestCase):
    def test_flags_heavy_selling_only(self):
        alarms = brief_data.flow_alarms(boot_with_flow(), wide_table(), squad_ids={1})
        self.assertEqual([a["name"] for a in alarms], ["P1"])     # P2's selling is under the 5,000 floor
        self.assertTrue(alarms[0]["owned"])
        self.assertEqual(alarms[0]["net_transfers"], -60000)
        self.assertLess(alarms[0]["ownership_change_pct"], -30)

    def test_flagged_or_doubtful_players_are_left_to_fpl_flags(self):
        w = wide_table()
        w.loc[0, "status"] = "d"
        self.assertEqual(brief_data.flow_alarms(boot_with_flow(), w, squad_ids={1}), [])

    def test_no_flow_data_is_quiet(self):
        self.assertEqual(brief_data.flow_alarms({"total_players": 0, "elements": []}, wide_table(), set()), [])


class ShadowGate(unittest.TestCase):
    def hist(self, diffs, top20_gap=0.0):
        n = len(diffs)
        return pd.DataFrame({"gw": range(13, 13 + n), "rank_live": [0.50] * n,
                             "rank_shadow_flagged": [0.50 + d for d in diffs],
                             "top20_live": [4.5] * n, "top20_shadow_flagged": [4.5 + top20_gap] * n})

    def test_needs_six_gameweeks(self):
        self.assertFalse(review.shadow_gate(self.hist([0.05] * 5))[0])

    def test_passes_with_consistent_gain(self):
        self.assertTrue(review.shadow_gate(self.hist([0.05, 0.03, 0.04, -0.01, 0.06, 0.02]))[0])

    def test_fails_if_top20_worse(self):
        self.assertFalse(review.shadow_gate(self.hist([0.05] * 6, top20_gap=-0.2))[0])

    def test_fails_if_too_few_wins(self):
        self.assertFalse(review.shadow_gate(self.hist([0.09, 0.09, 0.09, -0.01, -0.01, -0.01]))[0])


class ShadowOrigin(unittest.TestCase):
    def test_origin_is_first_unfinished_gameweek(self):
        boot = {"events": [{"id": g, "finished": g <= 11} for g in range(1, 39)]}
        self.assertEqual(shadow.signal_origin(boot), 12)   # GW12 in progress: features stop at GW11


if __name__ == "__main__":
    unittest.main()
