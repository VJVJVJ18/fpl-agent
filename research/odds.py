"""Bookmaker odds as a signal: implied goals for and against per team per fixture.

Source: football-data.co.uk Premier League CSVs, archived by the collector (odds/E0_<code>.csv
for whole seasons, history/<season>/odds_upcoming.csv.gz for upcoming matches).

Timing: only the pre-match prices are used (market average AvgH/AvgD/AvgA and Avg>2.5/Avg<2.5).
football-data.co.uk records them on Friday afternoon for weekend matches and Tuesday afternoon
for midweek ones, close to the FPL deadline. Closing prices (AvgC...) are set at kick-off and
include team news from after the deadline, so they are never used.

Method: remove the bookmaker margin from the 1X2 and over/under 2.5 prices, then find the two
Poisson goal rates (home, away) whose implied home-win, away-win and over-2.5 probabilities
match the market best (least squares).
"""
import gzip
import io
import urllib.request

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import poisson

from fpl import data

ODDS_BASE = "https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data/"
NAME_FIX = {"Man United": "Man Utd", "Tottenham": "Spurs", "Sheffield United": "Sheffield Utd",
            "Nott'm Forest": "Nott'm Forest", "Coventry": "Coventry City", "Hull": "Hull City",
            "Ipswich": "Ipswich Town"}
GOALS = np.arange(0, 11)
FEATURES = ["o_lam_for", "o_lam_against", "o_cs", "o_cs_minus_model", "o_att_ratio"]


def season_code(season):
    return season[2:4] + season[5:7]


def implied_rates(ph, pa, pover):
    """Poisson rates (home, away) matching margin-free P(home win), P(away win), P(over 2.5)."""
    def probs(lam):
        h, a = poisson.pmf(GOALS, lam[0]), poisson.pmf(GOALS, lam[1])
        m = np.outer(h, a)
        tot = np.add.outer(GOALS, GOALS)
        return np.array([np.tril(m, -1).sum(), np.triu(m, 1).sum(), m[tot > 2.5].sum()])
    target = np.array([ph, pa, pover])
    fit = least_squares(lambda lam: probs(lam) - target, x0=[1.4, 1.1], bounds=([0.05, 0.05], [6, 6]))
    return fit.x


def market_probs(df):
    """Margin-free probabilities from the pre-match average prices."""
    inv = 1 / df[["AvgH", "AvgD", "AvgA"]].astype(float)
    p = inv.div(inv.sum(axis=1), axis=0)
    ou = 1 / df[["Avg>2.5", "Avg<2.5"]].astype(float)
    over = ou["Avg>2.5"] / ou.sum(axis=1)
    return p["AvgH"], p["AvgA"], over


def match_rates(df):
    """One row per match with implied home and away goal rates."""
    df = df.dropna(subset=["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5"]).copy()
    ph, pa, po = market_probs(df)
    lam = np.array([implied_rates(*x) for x in zip(ph, pa, po)])
    df["lam_h"], df["lam_a"] = lam[:, 0], lam[:, 1]
    df["home"], df["away"] = df["HomeTeam"], df["AwayTeam"]
    return df[["Date", "home", "away", "lam_h", "lam_a"]]


def _fetch_csv(path, gz=False):
    raw = urllib.request.urlopen(ODDS_BASE + path, timeout=60).read()
    if gz:
        raw = gzip.decompress(raw)
    return pd.read_csv(io.BytesIO(raw))


def map_to_fpl(odds, teams, fixtures):
    """Attach FPL team ids and the gameweek (event) to each priced match."""
    tid = dict(zip(teams["name"], teams["id"]))

    def resolve(name):
        """FPL's team names vary by season (Ipswich vs Ipswich Town): exact, then fixed list, then prefix."""
        for cand in (name, NAME_FIX.get(name)):
            if cand in tid:
                return tid[cand]
        hits = [t for t in tid if t.startswith(name) or name.startswith(t)]
        return tid[hits[0]] if len(hits) == 1 else np.nan
    odds = odds.copy()
    odds["team_h"], odds["team_a"] = odds["home"].map(resolve), odds["away"].map(resolve)
    missing = odds[odds["team_h"].isna() | odds["team_a"].isna()]
    if len(missing):
        raise ValueError(f"unmapped teams {sorted(set(missing['home']) | set(missing['away']))}")
    odds = odds.astype({"team_h": int, "team_a": int})
    fx = fixtures.dropna(subset=["event"])[["event", "team_h", "team_a"]].astype(int)
    return odds.merge(fx, on=["team_h", "team_a"], how="inner")   # each pairing is unique per season


def season_fixture_rates(season):
    """Implied rates per FPL fixture for a past or current season."""
    odds = match_rates(_fetch_csv(f"odds/E0_{season_code(season)}.csv"))
    return map_to_fpl(odds, data._csv(season, "teams.csv"), data.past_fixtures(season))


def team_side(rates):
    """One row per team per match: keyed by (event, opp, home) from that team's point of view."""
    h = pd.DataFrame({"gw": rates["event"], "opp": rates["team_a"], "home": 1.0,
                      "o_lam_for": rates["lam_h"], "o_lam_against": rates["lam_a"]})
    a = pd.DataFrame({"gw": rates["event"], "opp": rates["team_h"], "home": 0.0,
                      "o_lam_for": rates["lam_a"], "o_lam_against": rates["lam_h"]})
    return pd.concat([h, a], ignore_index=True)


def add_features(P, rates_by_season):
    """Odds features on panel rows for the next gameweek only (h == 0): prices for later
    gameweeks do not exist at the deadline. Keyed on (gameweek, opponent, venue), which is unique
    except in the rare double gameweek where a team is away twice."""
    out = []
    for season, d in P.groupby("season"):
        r = rates_by_season.get(season)
        d = d.copy()
        if r is None:
            for c in FEATURES:
                d[c] = np.nan
            out.append(d)
            continue
        side = team_side(r).drop_duplicates(["gw", "opp", "home"])
        d = d.merge(side, on=["gw", "opp", "home"], how="left")
        d.loc[d["h"] != 0, ["o_lam_for", "o_lam_against"]] = np.nan
        out.append(d)
    P = pd.concat(out, ignore_index=True)
    P["o_cs"] = np.exp(-P["o_lam_against"])
    P["o_cs_minus_model"] = P["o_cs"] - P["cs_prob"]
    P["o_att_ratio"] = P["o_lam_for"] / P["team_xgf6"].clip(lower=0.3)
    return P
