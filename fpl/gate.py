"""Daily gate: decides what this morning's check does. Cheap: two small downloads.

Prints one line per action:
  STALE ...       collector data is over 6 hours old: refresh it before anything else
  REVIEW GW<n>    a finished gameweek (FPL data final) has not been reviewed yet
  BRIEF GW<n> ... the next deadline is within 36 hours: run the full brief
  SKIP ...        nothing to do today (printed only when there is no other action)
"""
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import data

ROOT = Path(__file__).resolve().parent.parent
WINDOW_HOURS = 36
STALE_HOURS = 6
FIRST_REVIEW_GW = 12  # first gameweek with logged predictions
UK = ZoneInfo("Europe/London")


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def reviewed(season=data.CURRENT_SEASON):
    path = ROOT / "reviews" / f"{season}.csv"
    if not path.exists():
        return set()
    with path.open() as f:
        return {int(r["gw"]) for r in csv.DictReader(f)}


def decide(boot, meta, now, done_reviews, briefed):
    actions = []
    age = (now - _ts(meta["fetched_at"])).total_seconds() / 3600
    if age > STALE_HOURS:
        actions.append(f"STALE data is {age:.0f}h old: refresh the collector, then run the gate again")
    for e in boot["events"]:
        if e["finished"] and e["data_checked"] and e["id"] >= FIRST_REVIEW_GW and e["id"] not in done_reviews:
            actions.append(f"REVIEW GW{e['id']}")
    future = [e for e in boot["events"] if _ts(e["deadline_time"]) > now]
    if not future:
        return actions or ["SKIP season finished"]
    nxt = future[0]
    hours = (_ts(nxt["deadline_time"]) - now).total_seconds() / 3600
    when = _ts(nxt["deadline_time"]).astimezone(UK).strftime("%a %d %b %H:%M")
    if hours <= WINDOW_HOURS:
        kind = "update: a brief for this gameweek was already sent" if nxt["id"] in briefed else "first brief"
        actions.append(f"BRIEF GW{nxt['id']} deadline {when} UK, in {hours:.1f}h ({kind})")
    if not any(a.split()[0] in ("REVIEW", "BRIEF", "STALE") for a in actions):
        actions.append(f"SKIP next deadline GW{nxt['id']} {when} UK, in {hours:.0f}h")
    return actions


def main():
    boot = data._json(data.COLLECTOR, "bootstrap.json")
    meta = data._json(data.COLLECTOR, "meta.json")
    pred_dir = ROOT / "predictions" / data.CURRENT_SEASON
    briefed = {int(p.stem[2:]) for p in pred_dir.glob("gw*.csv")} if pred_dir.exists() else set()
    for line in decide(boot, meta, datetime.now(timezone.utc), reviewed(), briefed):
        print(line)


if __name__ == "__main__":
    sys.exit(main())
