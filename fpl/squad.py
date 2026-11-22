"""Rebuild the current FPL squad from public data: players, selling prices,
bank, free transfers (estimated), chips left and legality checks.

Prices are in tenths of £1m, as the FPL API gives them (55 = £5.5m).

Usage:
  python -m fpl.squad data/                      # local copy of the data branch
  python -m fpl.squad https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data
  add --json for machine-readable output
"""
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3
MAX_FREE_TRANSFERS = 5
# Used only if the API stops listing chip windows: one set per half.
DEFAULT_CHIPS = [
    {"name": n, "start_event": s, "stop_event": e}
    for n in ("wildcard", "freehit", "bboost", "3xc")
    for s, e in ((1, 19), (20, 38))
]


def selling_price(bought, now):
    """FPL rule: you keep half of any rise, rounded down to £0.1m, and take the full fall."""
    return bought + (now - bought) // 2 if now > bought else now


def money(tenths):
    return f"£{tenths / 10:.1f}m"


def load(src):
    """Read the collector's files from a folder or from the data branch URL."""
    def read(name):
        if src.startswith("http"):
            with urllib.request.urlopen(f"{src.rstrip('/')}/{name}", timeout=30) as r:
                return json.load(r)
        return json.loads((Path(src) / name).read_text(encoding="utf-8"))

    data = {k: read(f"{k}.json") for k in ("meta", "bootstrap", "entry", "history", "transfers")}
    data["picks"] = {}
    for row in data["history"].get("current", []):
        try:
            data["picks"][row["event"]] = read(f"picks/{row['event']}.json")
        except Exception:  # a missing gameweek is reported by the checks below
            pass
    return data


def free_transfers_by_event(history):
    """Estimate free transfers available for each gameweek.

    Rule: +1 per gameweek, at most 5 banked. In a Wildcard or Free Hit week the
    banked transfers are kept but no +1 is earned. Checked against Ekipa Nervno's
    real history (3 free transfers for GW11, 2 for GW12). FPL has also topped
    everyone up by decree before (5 free transfers in 2025/26), which this cannot
    see, so the result is an estimate to confirm against the Transfers screen.
    Returns {event: free transfers available for that event}.
    """
    chip_at = {c["event"]: c["name"] for c in history.get("chips", [])}
    rows = sorted(history.get("current", []), key=lambda r: r["event"])
    ft_for = {}
    ft = None
    for row in rows:
        gw = row["event"]
        if ft is None:  # first gameweek played: squad built freely, then 1 free transfer
            ft = 1
            ft_for[gw + 1] = ft
            continue
        if chip_at.get(gw) in ("wildcard", "freehit"):
            pass  # banked transfers kept, no +1 this week
        else:
            ft = min(MAX_FREE_TRANSFERS, max(0, ft - row["event_transfers"]) + 1)
        ft_for[gw + 1] = ft
    return ft_for


def chips_left(bootstrap, history, event):
    windows = [c for c in (bootstrap.get("chips") or DEFAULT_CHIPS)
               if c["start_event"] <= event <= c["stop_event"]]
    used = history.get("chips", [])
    left = []
    for w in windows:
        n_used = sum(1 for c in used if c["name"] == w["name"]
                     and w["start_event"] <= c["event"] <= w["stop_event"])
        if n_used < w.get("number", 1):
            left.append({"name": w["name"], "expires_after_gw": w["stop_event"]})
    return left


def rebuild(data):
    bootstrap, history, transfers, picks = (
        data["bootstrap"], data["history"], data["transfers"], data["picks"])
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    fh_events = {c["event"] for c in history.get("chips", []) if c["name"] == "freehit"}
    notes = []

    if not picks:
        raise ValueError("no picks found: has the first deadline passed and the collector run?")
    last = max(picks)
    base = last
    if picks[last].get("active_chip") == "freehit":
        # After a Free Hit the squad and bank revert to the week before.
        earlier = [g for g in picks if g < last]
        if not earlier:
            raise ValueError(f"Free Hit in GW{last} but no earlier picks to revert to")
        base = max(earlier)
        notes.append(f"GW{last} was a Free Hit: squad and bank reverted to GW{base}")

    squad = [p["element"] for p in picks[base]["picks"]]
    bank = picks[base]["entry_history"]["bank"]

    # Transfers made after the last deadline, if the public list already shows them.
    pending = sorted((t for t in transfers if t["event"] > last), key=lambda t: t["time"])
    for t in pending:
        if t["element_out"] in squad:
            squad.remove(t["element_out"])
            squad.append(t["element_in"])
            bank += t["element_out_cost"] - t["element_in_cost"]
    if pending:
        notes.append(f"applied {len(pending)} transfer(s) made since the GW{last} deadline")

    def bought_at(el_id):
        ins = [t for t in transfers if t["element_in"] == el_id and t["event"] not in fh_events]
        if ins:
            return max(ins, key=lambda t: t["time"])["element_in_cost"], "transfer"
        e = elements[el_id]  # never transferred in: part of the original squad
        return e["now_cost"] - e["cost_change_start"], "start price"

    players = []
    for el_id in squad:
        e = elements[el_id]
        bought, how = bought_at(el_id)
        players.append({
            "id": el_id,
            "name": e["web_name"],
            "team": teams[e["team"]],
            "pos": POSITIONS[e["element_type"]],
            "element_type": e["element_type"],
            "bought": bought,
            "bought_source": how,
            "now": e["now_cost"],
            "sell": selling_price(bought, e["now_cost"]),
            "status": e["status"],
            "chance_next": e["chance_of_playing_next_round"],
            "news": e["news"],
        })
    players.sort(key=lambda p: (p["element_type"], -p["sell"]))

    # Legality checks.
    problems = []
    shape = Counter(p["element_type"] for p in players)
    for et, n in SQUAD_SHAPE.items():
        if shape.get(et, 0) != n:
            problems.append(f"{POSITIONS[et]}: {shape.get(et, 0)} players, need {n}")
    for club, n in Counter(p["team"] for p in players).items():
        if n > MAX_PER_CLUB:
            problems.append(f"{club}: {n} players, max {MAX_PER_CLUB}")

    next_event = data["meta"].get("next_event") or last + 1
    ft_table = free_transfers_by_event(history)
    entry = data["entry"]
    return {
        "team_name": entry.get("name"),
        "overall_rank": entry.get("summary_overall_rank"),
        "total_points": entry.get("summary_overall_points"),
        "data_as_of": data["meta"].get("fetched_at"),
        "squad_from_gw": base,
        "next_event": next_event,
        "players": players,
        "bank": bank,
        "squad_selling_value": sum(p["sell"] for p in players) + bank,
        "free_transfers_estimate": ft_table.get(next_event),
        "free_transfers_by_event": ft_table,
        "chips_left": chips_left(bootstrap, history, next_event),
        "chips_used": history.get("chips", []),
        "problems": problems,
        "notes": notes,
    }


def report(r):
    lines = [
        f"{r['team_name']}  |  overall rank {r['overall_rank']:,}  |  data as of {r['data_as_of']}"
        if r["overall_rank"] else f"{r['team_name']}  |  data as of {r['data_as_of']}",
        f"Squad for GW{r['next_event']} (from GW{r['squad_from_gw']} picks)",
        "",
        f"{'Pos':<4}{'Player':<18}{'Club':<6}{'Bought':>8}{'Now':>8}{'Sell':>8}  Status",
    ]
    for p in r["players"]:
        flag = "" if p["status"] == "a" else f"{p['status']} {p['chance_next']}% {p['news']}"
        lines.append(f"{p['pos']:<4}{p['name'][:17]:<18}{p['team']:<6}"
                     f"{money(p['bought']):>8}{money(p['now']):>8}{money(p['sell']):>8}  {flag}")
    lines += [
        "",
        f"Bank {money(r['bank'])}  |  selling value incl. bank {money(r['squad_selling_value'])}",
        f"Free transfers for GW{r['next_event']} (estimate, confirm on the Transfers screen): "
        f"{r['free_transfers_estimate']}",
        "Chips left this half: " + (", ".join(
            f"{c['name']} (until GW{c['expires_after_gw']})" for c in r["chips_left"]) or "none"),
    ]
    lines += [f"NOTE: {n}" for n in r["notes"]]
    lines += [f"PROBLEM: {p}" for p in r["problems"]] or ["Squad is legal."]
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    result = rebuild(load(args[0] if args else "data"))
    print(json.dumps(result, indent=2) if "--json" in sys.argv else report(result))
