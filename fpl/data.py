"""Load FPL match data into one table: one row per player per fixture.

Past seasons come from the vaastav/Fantasy-Premier-League archive; the current
season comes from this repo's collector (the `data` branch). Downloads are
cached in ~/.cache/fpl-agent so daily runs stay fast.
"""
import io
import json
import urllib.request
from pathlib import Path

import pandas as pd

VAASTAV = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/{file}"
COLLECTOR = "https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data/"
CACHE = Path.home() / ".cache" / "fpl-agent"
POS = {"GK": "GK", "GKP": "GK", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
POS_BY_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
CURRENT_SEASON = "2026-27"

STAT_COLS = ["minutes", "starts", "goals", "assists", "xg", "xa", "xgc", "cs", "gc", "saves",
             "bonus", "bps", "dc", "yc", "rc", "og", "pen_saved", "pen_missed", "points"]
RENAME = {"goals_scored": "goals", "expected_goals": "xg", "expected_assists": "xa",
          "expected_goals_conceded": "xgc", "clean_sheets": "cs", "goals_conceded": "gc",
          "defensive_contribution": "dc", "yellow_cards": "yc", "red_cards": "rc",
          "own_goals": "og", "penalties_saved": "pen_saved", "penalties_missed": "pen_missed",
          "total_points": "points"}


def _fetch(url, cache_name=None, refresh=False):
    """Return bytes from a URL, cached on disk when cache_name is given."""
    if cache_name:
        path = CACHE / cache_name
        if path.exists() and not refresh:
            return path.read_bytes()
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if cache_name:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


def _csv(season, file):
    raw = _fetch(VAASTAV.format(season=season, file=file), f"{season}/{file.replace('/', '_')}")
    return pd.read_csv(io.BytesIO(raw), low_memory=False)


def load_past_season(season):
    """A finished season from the vaastav archive."""
    m = _csv(season, "gws/merged_gw.csv")
    teams = _csv(season, "teams.csv")
    raw = _csv(season, "players_raw.csv")
    m = m[m["position"].isin(POS)].rename(columns=RENAME)
    team_id = dict(zip(teams["name"], teams["id"]))
    team_code = dict(zip(teams["id"], teams["code"]))
    out = pd.DataFrame({
        "season": season,
        "gw": m["GW"].astype(int),
        "element": m["element"].astype(int),
        "code": m["element"].map(dict(zip(raw["id"], raw["code"]))),
        "name": m["name"],
        "pos": m["position"].map(POS),
        "team": m["team"].map(team_id),
        "opp": m["opponent_team"].astype(int),
        "home": m["was_home"].astype(str).str.lower().eq("true"),
        "fixture": m["fixture"].astype(int),
        "value": m["value"],
        "selected": m["selected"],
        "fpl_xp": pd.to_numeric(m.get("xP"), errors="coerce"),
    })
    for c in STAT_COLS:
        out[c] = pd.to_numeric(m[c], errors="coerce") if c in m else float("nan")
    out["team_code"] = out["team"].map(team_code)
    out["opp_code"] = out["opp"].map(team_code)
    return out.reset_index(drop=True)


def _json(base, name, refresh=True):
    if base.startswith("http"):
        return json.loads(_fetch(base + name))
    return json.loads((Path(base) / name).read_text(encoding="utf-8"))


def load_current_season(base=COLLECTOR):
    """This season from the collector: finished gameweeks only.

    The collector stores each player's stats per gameweek; a double gameweek is
    split evenly across its two fixtures (rare, and only used for history).
    Returns (matches, bootstrap, fixtures).
    """
    boot = _json(base, "bootstrap.json")
    fixtures = pd.DataFrame(_json(base, "fixtures.json"))
    els = pd.DataFrame(boot["elements"])
    team_code = {t["id"]: t["code"] for t in boot["teams"]}
    finished = [e["id"] for e in boot["events"] if e["finished"]]
    rows = []
    for gw in finished:
        live = _json(base, f"live/{gw}.json")
        stats = pd.DataFrame([{"element": x["id"], **x["stats"]} for x in live["elements"]]).rename(columns=RENAME)
        stats = stats.merge(els[["id", "code", "web_name", "element_type", "team"]],
                            left_on="element", right_on="id", how="inner")
        fx = fixtures[fixtures["event"] == gw]
        for team, grp in stats.groupby("team"):
            games = [(f["id"], f["team_a"], True) if f["team_h"] == team else (f["id"], f["team_h"], False)
                     for _, f in fx[(fx["team_h"] == team) | (fx["team_a"] == team)].iterrows()]
            if not games:
                continue
            n = len(games)
            for fid, opp, home in games:
                part = grp.copy()
                for c in STAT_COLS:
                    if c in part:
                        part[c] = pd.to_numeric(part[c], errors="coerce") / n
                part["fixture"], part["opp"], part["home"] = fid, opp, home
                rows.append(part)
    if not rows:
        return pd.DataFrame(), boot, fixtures
    s = pd.concat(rows, ignore_index=True)
    out = pd.DataFrame({
        "season": CURRENT_SEASON, "gw": None, "element": s["element"], "code": s["code"],
        "name": s["web_name"], "pos": s["element_type"].map(POS_BY_TYPE), "team": s["team"],
        "opp": s["opp"], "home": s["home"], "fixture": s["fixture"],
        "value": float("nan"), "selected": float("nan"), "fpl_xp": float("nan"),
    })
    fx_gw = dict(zip(fixtures["id"], fixtures["event"]))
    out["gw"] = s["fixture"].map(fx_gw).astype(int)
    for c in STAT_COLS:
        out[c] = pd.to_numeric(s[c], errors="coerce") if c in s else float("nan")
    out["team_code"] = out["team"].map(team_code)
    out["opp_code"] = out["opp"].map(team_code)
    return out, boot, fixtures


def past_fixtures(season):
    """Fixture list of a past season: event, team_h, team_a, id."""
    return _csv(season, "fixtures.csv")[["id", "event", "team_h", "team_a", "kickoff_time"]]


def team_codes(season):
    t = _csv(season, "teams.csv")
    return dict(zip(t["id"], t["code"]))
