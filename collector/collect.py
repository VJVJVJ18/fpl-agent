"""Fetch public FPL data for one entry and save it as JSON files.

Runs on GitHub Actions every few hours (see .github/workflows/collect.yml).
Only public endpoints are used: no login, no credentials.

Usage: python collector/collect.py [output_dir]

Exit codes:
  0  everything fetched and checked
  1  core data (bootstrap-static) could not be fetched; nothing new written
  2  core data saved, but something needs attention (see meta.json "problems")
"""
import csv
import gzip
import io
import json
import os
import random
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from datetime import datetime, timedelta, timezone
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


# Point-in-time history. FPL's API only ever shows the present: injury flags, news, prices
# and transfer counts are overwritten, and no public archive keeps them as they stood at
# each deadline. These files are the only record, so they are never deleted.
HISTORY_FIELDS = ["id", "status", "chance_of_playing_next_round", "chance_of_playing_this_round",
                  "now_cost", "selected_by_percent", "transfers_in_event", "transfers_out_event",
                  "ep_next", "form", "news_added", "penalties_order",
                  "corners_and_indirect_freekicks_order", "direct_freekicks_order"]
DENSE_WINDOW_H = 48   # every run in the 48 hours before a deadline; otherwise one snapshot a day

# Bookmaker prices (football-data.co.uk, free CSVs): the market's view of every match.
ODDS_UPCOMING = "https://www.football-data.co.uk/fixtures.csv"
ODDS_SEASON = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
ODDS_PAST = ["2223", "2324", "2425", "2526"]


def season_label(bootstrap):
    """'2026-27' from the first gameweek's deadline."""
    year = int(bootstrap["events"][0]["deadline_time"][:4])
    return f"{year}-{str(year + 1)[2:]}"


def season_code(label):
    """'2026-27' -> '2627' (football-data.co.uk's folder name)."""
    return label[2:4] + label[5:7]


def _read_csv_gz(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv_gz(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def snapshot_due(out, now, next_deadline, season):
    """True if this run should add a player snapshot (dense before deadlines, daily otherwise)."""
    if next_deadline:
        dl = datetime.fromisoformat(next_deadline.replace("Z", "+00:00"))
        if timedelta(0) <= dl - now <= timedelta(hours=DENSE_WINDOW_H):
            return True
    return not (out / "history" / season / "players" / f"{now:%Y-%m-%d}.csv.gz").exists()


def save_player_history(out, bootstrap, now, season, next_event):
    """Append one row per player, as they stand now, to today's snapshot file."""
    path = out / "history" / season / "players" / f"{now:%Y-%m-%d}.csv.gz"
    rows = _read_csv_gz(path)
    stamp = now.isoformat(timespec="minutes")
    rows += [{"fetched_at": stamp, "next_event": next_event, **{k: e.get(k) for k in HISTORY_FIELDS}}
             for e in bootstrap["elements"]]
    _write_csv_gz(path, rows, ["fetched_at", "next_event"] + HISTORY_FIELDS)
    return len(bootstrap["elements"])


def save_news_log(out, bootstrap, now, season):
    """Append a row whenever a player's status, chance of playing or news text changes."""
    path = out / "history" / season / "news.csv.gz"
    rows = _read_csv_gz(path)
    last = {}
    for r in rows:
        last[r["id"]] = (r["status"], r["chance_of_playing_next_round"], r["news"])
    added = 0
    stamp = now.isoformat(timespec="minutes")
    for e in bootstrap["elements"]:
        state = (str(e.get("status")), "" if e.get("chance_of_playing_next_round") is None
                 else str(e.get("chance_of_playing_next_round")), e.get("news") or "")
        if last.get(str(e["id"])) != state:
            rows.append({"fetched_at": stamp, "id": e["id"], "status": state[0],
                         "chance_of_playing_next_round": state[1], "news": state[2],
                         "news_added": e.get("news_added")})
            added += 1
    if added or not path.exists():
        _write_csv_gz(path, rows, ["fetched_at", "id", "status", "chance_of_playing_next_round", "news", "news_added"])
    return added


def fetch_text(url, tries=3):
    """GET a plain-text file (CSV) from outside the FPL API."""
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8-sig", errors="replace")
            time.sleep(PAUSE)
            return text
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == tries:
                raise RuntimeError(f"{url} -> {err}") from err
        time.sleep(5 * attempt)
    raise RuntimeError(f"{url} -> gave up")


def premier_league_rows(text):
    """Rows of a football-data.co.uk CSV that belong to the Premier League (Div E0)."""
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames or [], [r for r in reader if (r.get("Div") or "").strip() == "E0"]


def save_odds(out, season, now, fetch=None):
    """Season results with closing odds (refreshed daily), past seasons once, and a timestamped
    log of the upcoming fixtures' odds (a row is added only when a match's prices change)."""
    fetch = fetch or fetch_text
    notes = []
    odds = out / "odds"
    state = load_if_exists(odds / "meta.json") or {}
    today = f"{now:%Y-%m-%d}"
    for code in ODDS_PAST:
        if not (odds / f"E0_{code}.csv").exists():
            try:
                fields, rows = premier_league_rows(fetch(ODDS_SEASON.format(code=code)))
                if rows:
                    _write_plain_csv(odds / f"E0_{code}.csv", rows, fields)
            except RuntimeError as err:
                notes.append(f"odds {code}: {err}")
    code = season_code(season)
    if state.get("season_fetched") != today:
        try:
            fields, rows = premier_league_rows(fetch(ODDS_SEASON.format(code=code)))
            if rows:
                _write_plain_csv(odds / f"E0_{code}.csv", rows, fields)
            state["season_fetched"] = today
        except RuntimeError as err:
            notes.append(f"odds {code}: {err}")
    try:
        fields, rows = premier_league_rows(fetch(ODDS_UPCOMING))
        path = out / "history" / season / "odds_upcoming.csv.gz"
        log = _read_csv_gz(path)
        seen = {(r.get("Date"), r.get("HomeTeam"), r.get("AwayTeam")): r for r in log}
        prices = lambda r: {k: (r.get(k) or "") for k in fields if k}
        new = []
        for r in rows:
            key = (r.get("Date"), r.get("HomeTeam"), r.get("AwayTeam"))
            if key not in seen or prices(seen[key]) != prices(r):
                new.append({"fetched_at": now.isoformat(timespec="minutes"), **{k: v for k, v in r.items() if k}})
        if new or not path.exists():
            all_fields = ["fetched_at"] + list(dict.fromkeys(
                [f for r in log for f in r if f != "fetched_at"] + list(fields)))
            _write_csv_gz(path, log + new, all_fields)
        state["upcoming_rows_added"] = len(new)
    except RuntimeError as err:
        notes.append(f"odds upcoming: {err}")
    save(out, "odds/meta.json", state)
    return notes


def _write_plain_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


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

    # 5. Point-in-time history (flags, news, prices, transfer flow) and bookmaker odds.
    #    Warnings only: the brief still runs without them.
    now = datetime.now(timezone.utc)
    try:
        season = season_label(bootstrap)
        if snapshot_due(out, now, meta["next_deadline_utc"], season):
            meta["history_snapshot_rows"] = save_player_history(out, bootstrap, now, season, meta["next_event"])
        meta["news_changes"] = save_news_log(out, bootstrap, now, season)
        meta["warnings"] += save_odds(out, season, now)
    except (OSError, ValueError, KeyError) as err:
        meta["warnings"].append(f"history: {err}")

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
