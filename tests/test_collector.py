"""End-to-end check: the collector's output can be read by the squad rebuild.

The FPL API is faked, so this runs anywhere. Reuses the synthetic season from
test_squad.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
import collect  # noqa: E402

from fpl.squad import load, rebuild  # noqa: E402
from tests.test_squad import DATA  # noqa: E402

REQUIRED_EXTRA = {f: 0 for f in collect.REQUIRED_ELEMENT_FIELDS}


def fake_api(entry_name="Ekipa Nervno", fail=()):
    boot = copy.deepcopy(DATA["bootstrap"])
    boot["elements"] = [{**REQUIRED_EXTRA, **e} for e in boot["elements"]]
    boot["events"] = [
        {"id": g, "deadline_time": f"2026-0{8 + g // 4}-0{g}T10:00:00Z", "finished": g < 3,
         "data_checked": g < 3, "is_current": g == 3, "is_next": g == 4}
        for g in range(1, 6)]
    responses = {
        "bootstrap-static/": boot,
        "fixtures/": [],
        "entry/1891296/": {"name": entry_name, "summary_overall_rank": 12345},
        "entry/1891296/history/": DATA["history"],
        "entry/1891296/transfers/": DATA["transfers"],
    }
    for g in (1, 2, 3):
        responses[f"event/{g}/live/"] = {"elements": [{"id": 1, "stats": {"minutes": 90}, "explain": []}]}
        responses[f"entry/1891296/event/{g}/picks/"] = DATA["picks"][g]

    def get(path, tries=3):
        if path in fail or any(path.startswith(f[:-1]) for f in fail if f.endswith("*")):
            raise RuntimeError(f"{path} -> HTTP 403")
        if path.startswith("leagues-classic/314/standings/"):
            page = int(path.rsplit("=", 1)[1])
            return {"standings": {"results": [
                {"entry": 100_000 + r, "rank": r, "total": 1000 - r // 100}
                for r in range((page - 1) * 50 + 1, page * 50 + 1)]}}
        if path.startswith("entry/1") and path.endswith("/event/3/picks/") and len(path.split("/")[1]) == 6:
            eid = int(path.split("/")[1])
            # Everyone owns player 1; even ids captain him, odd ids captain player 2.
            return {"active_chip": None, "picks": [
                {"element": 1, "multiplier": 2 if eid % 2 == 0 else 1, "is_captain": eid % 2 == 0},
                {"element": 2, "multiplier": 1 if eid % 2 == 0 else 2, "is_captain": eid % 2 == 1},
                {"element": 3, "multiplier": 0, "is_captain": False}]}
        if path not in responses:
            raise RuntimeError(f"{path} -> HTTP 403")
        return copy.deepcopy(responses[path])
    return get


class Collector(unittest.TestCase):
    def run_collector(self, **kw):
        tmp = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(Path(__file__).resolve().parents[1])  # config.json lives at the repo root
        try:
            with mock.patch.object(collect, "get", fake_api(**kw)), \
                 mock.patch.object(sys, "argv", ["collect.py", tmp]):
                code = collect.main()
        finally:
            os.chdir(cwd)
        return code, Path(tmp)

    def test_output_feeds_squad_rebuild(self):
        code, out = self.run_collector()
        self.assertEqual(code, 0)
        meta = json.loads((out / "meta.json").read_text())
        self.assertEqual((meta["current_event"], meta["next_event"], meta["problems"]), (3, 4, []))
        live = json.loads((out / "live" / "1.json").read_text())
        self.assertNotIn("explain", live["elements"][0])  # slimmed
        r = rebuild(load(str(out)))
        self.assertEqual(r["squad_from_gw"], 2)
        self.assertEqual(r["problems"], [])

    def test_field_sample_and_target_line(self):
        code, out = self.run_collector()
        self.assertEqual(code, 0)
        meta = json.loads((out / "meta.json").read_text())
        self.assertEqual(meta["target"], {"rank": 10000, "total": 900, "event": 3})
        self.assertEqual(meta["top10k_events"], [3])
        field = json.loads((out / "top10k" / "3.json").read_text())
        self.assertEqual(field["teams"], 1000)
        p1 = field["players"]["1"]
        self.assertEqual(p1["own"], 1.0)
        self.assertAlmostEqual(p1["cap"], 0.5, delta=0.06)
        self.assertAlmostEqual(p1["eo"], 1 + p1["cap"], places=3)
        self.assertEqual(field["players"]["3"]["eo"], 0.0)

    def test_field_failure_is_a_warning_not_a_problem(self):
        code, out = self.run_collector(fail=("leagues-classic/*",))
        self.assertEqual(code, 0)
        meta = json.loads((out / "meta.json").read_text())
        self.assertEqual(meta["top10k_events"], [])
        self.assertEqual(len(meta["warnings"]), 2)

    def test_wrong_team_is_flagged(self):
        code, out = self.run_collector(entry_name="Someone Else")
        self.assertEqual(code, 2)
        self.assertIn("check entry_id", json.loads((out / "meta.json").read_text())["problems"][0])

    def test_blocked_api_fails_hard(self):
        code, out = self.run_collector(fail=("bootstrap-static/",))
        self.assertEqual(code, 1)
        self.assertFalse((out / "bootstrap.json").exists())

    def test_blocked_team_endpoints_flagged_but_core_saved(self):
        code, out = self.run_collector(fail=("entry/1891296/",))
        self.assertEqual(code, 2)
        self.assertTrue((out / "bootstrap.json").exists())


if __name__ == "__main__":
    unittest.main()
