"""FPL scoring, read from the game's own config (bootstrap.json game_config.scoring)."""

# 2026/27 values from game_config.scoring, used when no bootstrap is passed.
DEFAULT = {
    "goals": {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4},
    "cs": {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
    "gc_per2": {"GK": -1, "DEF": -1, "MID": 0, "FWD": 0},
    "dc_points": {"GK": 0, "DEF": 2, "MID": 2, "FWD": 2},
    # Actions needed for DefCon points (not in the config): 10 CBIT for defenders,
    # 12 CBIRT for midfielders and forwards. Verified against 2025/26 points.
    "dc_threshold": {"GK": 999, "DEF": 10, "MID": 12, "FWD": 12},
    "assist": 3, "save_per3": 1, "yc": -1, "rc": -3, "og": -2, "pen_saved": 5, "pen_missed": -2,
    "short_play": 1, "long_play": 2,
}
KEY = {"GKP": "GK", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}


def from_bootstrap(boot):
    s = (boot or {}).get("game_config", {}).get("scoring")
    if not s:
        return DEFAULT
    by_pos = lambda d: {KEY[k]: v for k, v in d.items() if k in KEY}
    return {**DEFAULT,
            "goals": by_pos(s["goals_scored"]), "cs": by_pos(s["clean_sheets"]),
            "gc_per2": by_pos(s["goals_conceded"]), "dc_points": by_pos(s["defensive_contribution"]),
            "assist": s["assists"], "save_per3": s["saves"], "yc": s["yellow_cards"],
            "rc": s["red_cards"], "og": s["own_goals"], "pen_saved": s["penalties_saved"],
            "pen_missed": s["penalties_missed"], "short_play": s["short_play"], "long_play": s["long_play"]}


def points_from_stats(df, sc=DEFAULT):
    """Recompute FPL points from match stats (used to verify the scoring rules)."""
    pos = df["pos"]
    m = df["minutes"]
    app = (m > 0) * sc["short_play"] + (m >= 60) * (sc["long_play"] - sc["short_play"])
    pts = (app + df["goals"] * pos.map(sc["goals"]) + df["assists"] * sc["assist"]
           + df["cs"] * pos.map(sc["cs"]) + (df["gc"] // 2) * pos.map(sc["gc_per2"])
           + (df["saves"] // 3) * sc["save_per3"] + df["yc"] * sc["yc"] + df["rc"] * sc["rc"]
           + df["og"] * sc["og"] + df["pen_saved"] * sc["pen_saved"]
           + df["pen_missed"] * sc["pen_missed"] + df["bonus"])
    dc_hit = df["dc"].fillna(0) >= pos.map(sc["dc_threshold"])
    return pts + dc_hit * pos.map(sc["dc_points"])
