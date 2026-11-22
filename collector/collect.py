"""Fetch public FPL data for one entry and save it as JSON files.

Runs on GitHub Actions every few hours (see .github/workflows/collect.yml).
Only public endpoints are used: no login, no credentials.

Usage: python collector/collect.py [output_dir]

Exit codes:
  0  everything fetched and checked
  1  core data (bootstrap-static) could not be fetched; nothing new written
  2  core data saved, but something needs attention (see meta.json "problems")
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://fantasy.premierleague.com/api/"
HEADERS = {
    "User-Agent": "fpl-agent personal data collector (github.com/VJVJVJ18/fpl-agent)",
    "Accept": "application/json",
}
PAUSE = 1.0  # seconds between requests, to keep the load light

# Fields the model relies on. If FPL renames one, the run is flagged instead of
# quietly producing wrong numbers.
REQUIRED_ELEMENT_FIELDS = [
    "id", "web_name", "team", "element_type", "now_cost", "cost_change_start",
    "status", "chance_of_playing_next_round", "news", "news_added", "minutes",
    "starts", "expected_goals", "expected_assists", "expected_goals_conceded",
    "defensive_contribution", "selected_by_percent", "penalties_order", "ep_next",
]
REQUIRED_EVENT_FIELDS = ["id", "deadline_time", "finished", "data_checked", "is_current", "is_next"]


def get(path, tries=3):
    """GET an API path and return parsed JSON. Retries transient failures."""
    url = API + path
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            time.sleep(PAUSE)
            return data
        except urllib.error.HTTPError as err:
            if err.code in (403, 404) or attempt == tries:
                raise RuntimeError(f"{url} -> HTTP {err.code}") from err
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            if attempt == tries:
                raise RuntimeError(f"{url} -> {err}") from err
        time.sleep(5 * attempt)
    raise RuntimeError(f"{url} -> gave up")


def save(out, name, obj):
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def load_if_exists(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check_schema(bootstrap):
    element, event = bootstrap["elements"][0], bootstrap["events"][0]
    missing = [f"elements.{k}" for k in REQUIRED_ELEMENT_FIELDS if k not in element]
    missing += [f"events.{k}" for k in REQUIRED_EVENT_FIELDS if k not in event]
    return missing


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    entry_id = int(os.environ.get("FPL_ENTRY_ID") or config["entry_id"])
    expected_name = config.get("expected_team_name")
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entry_id": entry_id,
        "problems": [],
    }

    # 1. Core game data. Without this there is nothing useful to publish.
    try:
        bootstrap = get("bootstrap-static/")
    except RuntimeError as err:
        print(f"FATAL: {err}")
        return 1
    meta["problems"] += [f"schema: missing {m}" for m in check_schema(bootstrap)]
    save(out, "bootstrap.json", bootstrap)

    events = bootstrap["events"]
    started = [e for e in events if e["finished"] or e["is_current"]]
    nxt = next((e for e in events if e["is_next"]), None)
    meta["current_event"] = started[-1]["id"] if started else None
    meta["next_event"] = nxt["id"] if nxt else None
    meta["next_deadline_utc"] = nxt["deadline_time"] if nxt else None

    try:
        save(out, "fixtures.json", get("fixtures/"))
    except RuntimeError as err:
        meta["problems"].append(str(err))

    # 2. Per-gameweek stats for every player. Kept until FPL marks the gameweek
    #    final (data_checked), then never fetched again.
    for e in started:
        path = out / "live" / f"{e['id']}.json"
        cached = load_if_exists(path)
        if cached and cached.get("data_checked"):
            continue
        try:
            live = get(f"event/{e['id']}/live/")
        except RuntimeError as err:
            meta["problems"].append(str(err))
            continue
        save(out, f"live/{e['id']}.json", {
            "event": e["id"],
            "data_checked": e["data_checked"],
            "elements": [{"id": x["id"], "stats": x["stats"]} for x in live["elements"]],
        })

    # 3. The manager's own team: public endpoints only.
    try:
        entry = get(f"entry/{entry_id}/")
        save(out, "entry.json", entry)
        meta["team_name"] = entry.get("name")
        if expected_name and entry.get("name") != expected_name:
            meta["problems"].append(
                f"entry {entry_id} is '{entry.get('name')}', expected '{expected_name}': check entry_id in config.json")
        history = get(f"entry/{entry_id}/history/")
        save(out, "history.json", history)
        save(out, "transfers.json", get(f"entry/{entry_id}/transfers/"))
        # Picks are fixed once a deadline passes, so each gameweek is fetched once.
        played = {row["event"] for row in history.get("current", [])}
        for e in started:
            if e["id"] not in played or (out / "picks" / f"{e['id']}.json").exists():
                continue
            try:
                save(out, f"picks/{e['id']}.json", get(f"entry/{entry_id}/event/{e['id']}/picks/"))
            except RuntimeError as err:
                meta["problems"].append(str(err))
    except RuntimeError as err:
        meta["problems"].append(str(err))

    save(out, "meta.json", meta)
    print(f"Team: {meta.get('team_name')} | current GW {meta['current_event']} | "
          f"next GW {meta['next_event']} deadline {meta['next_deadline_utc']} UTC")
    for p in meta["problems"]:
        print(f"PROBLEM: {p}")
    return 2 if meta["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
