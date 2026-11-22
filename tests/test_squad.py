"""Tests for the squad rebuild on a small synthetic season.

GW1: original squad, players 1-15.
GW2: transfer 3 -> 16.
GW3: Free Hit, with 8 -> 17 shown in the transfer list (must be ignored).
After the GW3 deadline: pending transfer 4 -> 19.
Run with: python -m unittest discover tests
"""
import copy
import unittest

from fpl.squad import chips_left, free_transfers_by_event, rebuild, selling_price

TYPES = {1: 1, 2: 1, 18: 1, **{i: 2 for i in (3, 4, 5, 6, 7, 16, 19)},
         **{i: 3 for i in (8, 9, 10, 11, 12, 17)}, 13: 4, 14: 4, 15: 4}
# (now_cost, cost_change_start)
PRICES = {1: (55, 3), 2: (40, -2), 16: (47, 2), 19: (40, 0), 17: (60, 0)}


def element(i):
    now, change = PRICES.get(i, (50, 0))
    return {"id": i, "web_name": f"P{i}", "team": i, "element_type": TYPES[i],
            "now_cost": now, "cost_change_start": change, "status": "a",
            "chance_of_playing_next_round": None, "news": ""}


def picks(squad, bank, chip=None):
    return {"active_chip": chip, "entry_history": {"bank": bank},
            "picks": [{"element": e} for e in squad]}


GW1 = list(range(1, 16))
GW2 = [16 if e == 3 else e for e in GW1]
GW3 = [17 if e == 8 else e for e in GW2]

DATA = {
    "meta": {"next_event": 4, "fetched_at": "2026-11-23T08:15:00+00:00"},
    "bootstrap": {
        "elements": [element(i) for i in TYPES],
        "teams": [{"id": i, "short_name": f"T{i}"} for i in TYPES],
        "chips": [{"name": n, "number": 1, "start_event": 1, "stop_event": 19}
                  for n in ("wildcard", "freehit", "bboost", "3xc")],
    },
    "entry": {"name": "Test XI", "summary_overall_rank": 12345, "summary_overall_points": 300},
    "history": {
        "current": [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 1},
                    {"event": 3, "event_transfers": 1}],
        "chips": [{"name": "freehit", "event": 3}],
    },
    "transfers": [
        {"element_in": 16, "element_in_cost": 45, "element_out": 3, "element_out_cost": 50,
         "event": 2, "time": "2026-08-25T10:00:00Z"},
        {"element_in": 17, "element_in_cost": 60, "element_out": 8, "element_out_cost": 50,
         "event": 3, "time": "2026-09-01T10:00:00Z"},
        {"element_in": 19, "element_in_cost": 40, "element_out": 4, "element_out_cost": 50,
         "event": 4, "time": "2026-09-08T10:00:00Z"},
    ],
    "picks": {1: picks(GW1, 0), 2: picks(GW2, 5), 3: picks(GW3, 0, chip="freehit")},
}


class SellingPrice(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(selling_price(50, 53), 51)  # +0.3m rise: keep 0.1m
        self.assertEqual(selling_price(50, 51), 50)  # +0.1m rise: keep nothing
        self.assertEqual(selling_price(50, 54), 52)  # +0.4m rise: keep 0.2m
        self.assertEqual(selling_price(50, 48), 48)  # fall: take it all
        self.assertEqual(selling_price(50, 50), 50)


class Rebuild(unittest.TestCase):
    def setUp(self):
        self.r = rebuild(copy.deepcopy(DATA))
        self.by_id = {p["id"]: p for p in self.r["players"]}

    def test_free_hit_reverts_and_pending_transfer_applied(self):
        self.assertEqual(self.r["squad_from_gw"], 2)
        self.assertEqual(sorted(self.by_id), sorted([19 if e == 4 else e for e in GW2]))
        self.assertNotIn(17, self.by_id)  # Free Hit player gone
        self.assertIn(8, self.by_id)      # original player back

    def test_bank_after_pending_transfer(self):
        self.assertEqual(self.r["bank"], 5 + 50 - 40)

    def test_purchase_and_selling_prices(self):
        self.assertEqual((self.by_id[1]["bought"], self.by_id[1]["sell"]), (52, 53))   # riser from start
        self.assertEqual((self.by_id[2]["bought"], self.by_id[2]["sell"]), (42, 40))   # faller
        self.assertEqual((self.by_id[16]["bought"], self.by_id[16]["sell"]), (45, 46))  # bought by transfer
        self.assertEqual(self.by_id[8]["bought_source"], "start price")  # FH transfer ignored

    def test_squad_is_legal(self):
        self.assertEqual(self.r["problems"], [])

    def test_free_transfers(self):
        # 1 for GW2; used 1 -> 1 for GW3; Free Hit keeps it but earns no +1 -> 1 for GW4
        self.assertEqual(free_transfers_by_event(DATA["history"]), {2: 1, 3: 1, 4: 1})
        self.assertEqual(self.r["free_transfers_estimate"], 1)

    def test_free_transfers_match_real_history(self):
        # Ekipa Nervno 2026/27 to GW11: Triple Captain GW3, Wildcard GW4.
        # Known from the Transfers screen: 3 free transfers for GW11, 2 for GW12.
        used = {1: 0, 2: 0, 3: 1, 4: 0, 5: 1, 6: 1, 7: 0, 8: 0, 9: 3, 10: 0, 11: 2}
        hist = {"current": [{"event": g, "event_transfers": n} for g, n in used.items()],
                "chips": [{"name": "3xc", "event": 3}, {"name": "wildcard", "event": 4}]}
        ft = free_transfers_by_event(hist)
        self.assertEqual((ft[11], ft[12]), (3, 2))

    def test_cap_at_five(self):
        hist = {"current": [{"event": g, "event_transfers": 0} for g in range(1, 10)], "chips": []}
        self.assertEqual(free_transfers_by_event(hist)[10], 5)

    def test_chips_left(self):
        left = {c["name"] for c in chips_left(DATA["bootstrap"], DATA["history"], 4)}
        self.assertEqual(left, {"wildcard", "bboost", "3xc"})

    def test_club_limit_flagged(self):
        data = copy.deepcopy(DATA)
        for e in data["bootstrap"]["elements"]:
            if e["id"] in (9, 10, 11, 12):
                e["team"] = 9
        self.assertTrue(any("max 3" in p for p in rebuild(data)["problems"]))


if __name__ == "__main__":
    unittest.main()
