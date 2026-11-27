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
import random
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
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

# Top-10k field: who the managers Vuk has to beat actually own and captain.
OVERALL_LEAGUE = 314       # FPL's overall league
TARGET_RANK = 10_000       # Vuk's goal
PAGE_SIZE = 50             # entries per standings page
PAGE_STEP = 4              # sample every 4th standings page (50 pages across ranks 1-10,000)
PER_PAGE = 20              # entries sampled per page -> 1,000 teams, ownership error under 1.6 points
MIN_SAMPLE = 300           # below this the sample is not saved and is retried next run
WORKERS = 5                # parallel picks requests (each still pauses between calls)


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


def standings(page, fetch):
    return fetch(f"leagues-classic/{OVERALL_LEAGUE}/standings/?page_standings={page}")["standings"]["results"]


def target_line(fetch):
    """Total points of the manager at the target rank (last row of page 200)."""
    rows = [r for r in standings(TARGET_RANK // PAGE_SIZE, fetch) if r["rank"] <= TARGET_RANK]
    last = max(rows, key=lambda r: r["rank"])
    return {"rank": last["rank"], "total": last["total"]}


def field_sample(gw, fetch, seed=None):
    """Ownership, captaincy and effective ownership (mean multiplier) in a sample of top-10k teams.

    Systematic sample by rank: every PAGE_STEP-th standings page from a random offset,
    PER_PAGE random entries from each. Picks for a gameweek are public once its deadline passes.
    """
    rng = random.Random(seed if seed is not None else gw)
    entries = []
    for page in range(1 + rng.randrange(PAGE_STEP), TARGET_RANK // PAGE_SIZE + 1, PAGE_STEP):
        rows = [r for r in standings(page, fetch) if r["rank"] <= TARGET_RANK]
        entries += rng.sample(rows, min(PER_PAGE, len(rows)))

    def picks(row):
        try:
            return fetch(f"entry/{row['entry']}/event/{gw}/picks/")
        except RuntimeError:
            return None

    with ThreadPoolExecutor(WORKERS) as pool:
        results = [r for r in pool.map(picks, entries) if r and r.get("picks")]
    n = len(results)
    if n < MIN_SAMPLE:
        raise RuntimeError(f"top-10k sample for GW{gw}: only {n} of {len(entries)} teams fetched")
    own, cap, mult, chips = {}, {}, {}, {}
    for r in results:
        chip = r.get("active_chip") or "none"
        chips[chip] = chips.get(chip, 0) + 1
        for p in r["picks"]:
            e = p["element"]
            own[e] = own.get(e, 0) + 1
            mult[e] = mult.get(e, 0) + p["multiplier"]
            cap[e] = cap.get(e, 0) + (1 if p["is_captain"] else 0)
    return {
        "event": gw, "teams": n, "requested": len(entries), "rank_max": TARGET_RANK,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chips": {k: round(v / n, 4) for k, v in chips.items()},
        "players": {str(e): {"own": round(own[e] / n, 4), "cap": round(cap[e] / n, 4),
                             "eo": round(mult[e] / n, 4)} for e in own},
    }


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

    # 4. The field: the target rank's score each run, and a top-10k ownership sample once
    #    per gameweek after its deadline. Failures here are warnings, not problems.
    meta["warnings"] = []
    try:
        meta["target"] = {**target_line(get), "event": meta["current_event"]}
    except (RuntimeError, KeyError, ValueError) as err:
        meta["warnings"].append(f"target line: {err}")
    done = sorted(int(p.stem) for p in (out / "top10k").glob("*.json")) if (out / "top10k").exists() else []
    gw = meta["current_event"]
    if gw and gw not in done:
        try:
            save(out, f"top10k/{gw}.json", field_sample(gw, get))
            done.append(gw)
        except (RuntimeError, KeyError, ValueError) as err:
            meta["warnings"].append(str(err))
    meta["top10k_events"] = sorted(done)

    save(out, "meta.json", meta)
    print(f"Team: {meta.get('team_name')} | current GW {meta['current_event']} | "
          f"next GW {meta['next_event']} deadline {meta['next_deadline_utc']} UTC")
    for p in meta["problems"]:
        print(f"PROBLEM: {p}")
    for w in meta["warnings"]:
        print(f"WARNING: {w}")
    return 2 if meta["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
