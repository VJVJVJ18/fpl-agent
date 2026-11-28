"""Research lab: rebuild the panel, scan signals, test forecast models walk-forward, test
decisions with the real optimiser, and write research/REPORT.md with a verdict per candidate.

  pip install -r research/requirements.txt
  python -m research.run            # about 15 minutes

Promotion gate (a candidate replaces the live model only if Vuk agrees after seeing this):
  1. Forecast gate, on both periods no setting was ever tuned on (last season's GW25-38 and
     this season): rank IC higher than the base model with t >= 2.5, and top-20 points not lower.
  2. Decision test, same-state replays of two seasons with the agent's own optimiser:
       PROMOTE  mean gain > 0 and the 90% interval above 0
       SHADOW   mean gain >= -0.25 points per GW: tracked live each GW, not used for decisions
       REJECT   otherwise
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research import combiner, decisions, panel, scan  # noqa: E402

REPORT = ROOT / "research" / "REPORT.md"
DECISION_SEASONS = ["2024-25", "2025-26"]
CANDIDATES = ["stats", "full", "stats_flow", "overlay"]
REFERENCE_ONLY = {"full"}   # uses ownership levels: shown for comparison, never promoted (Vuk's no-consensus rule)


def _same_state(args):
    season, model = args
    return decisions.same_state(season, model)


def _replay(args):
    season, model = args
    return decisions.replay(season, model)


def gate(res):
    per = combiner.periods(res)
    untouched = [k for k in per if "untouched" in k]
    out = {}
    for v in CANDIDATES:
        checks = [combiner.paired(res[per[k]], "pred_base", "pred_" + v) for k in untouched]
        out[v] = {"forecast_pass": all(c["d_rank"] > 0 and c["t_rank"] >= 2.5 and c["d_top20"] >= 0 for c in checks),
                  "checks": dict(zip(untouched, checks))}
    return out


def decision_verdict(frames, rng=np.random.default_rng(3)):
    d = pd.concat(frames)
    x = (d["model"] - d["base"]) / decisions.SCORE_GWS
    boots = [rng.choice(x.values, len(x)).mean() for _ in range(5000)]
    lo, hi = np.percentile(boots, [5, 95])
    mean = x.mean()
    verdict = "PROMOTE" if mean > 0 and lo > 0 else ("SHADOW" if mean >= -0.25 else "REJECT")
    return {"pts_per_gw": mean, "t": mean / x.std(ddof=1) * np.sqrt(len(x)), "ci90": (lo, hi),
            "weeks": len(x), "sd": x.std(ddof=1), "verdict": verdict,
            "by_season": d.assign(x=x).groupby("season")["x"].mean().to_dict()}


def ownership_check(P):
    """Does the old ownership term in the calibration help the picks that matter?"""
    from fpl import backtest, model
    one = P[(P["h"] == 0) & P["pool"] & P["points"].notna()].copy()
    scale = {s: backtest.ownership_scale(backtest.Season(s)) for s in ["2023-24", "2024-25", "2025-26"]}
    scale["2026-27"] = scale["2025-26"]
    one["own"] = (np.expm1(one["log_sel"]) / one["season"].map(scale)).clip(0, 100)
    cal = model.load_calibration()
    (a1, b1), (a2, b2, c2) = cal["xp_only"], cal["with_own"]
    one["no_own"] = a1 * one["p_play"] + b1 * one["xp"]
    one["with_own"] = a2 * one["p_play"] + b2 * one["xp"] + c2 * np.log1p(one["own"]) * one["p_play"]
    oos = one[~((one["season"] == "2025-26") & (one["origin"] <= 24))]
    rows = []
    for k in (11, 20, 40):
        top = lambda c: oos.sort_values(c, ascending=False).groupby(["season", "origin"]).head(k)
        d = (top("with_own").groupby(["season", "origin"])["points"].mean()
             - top("no_own").groupby(["season", "origin"])["points"].mean()).dropna()
        rows.append({"top k by xP": k, "with minus without ownership (pts/player/GW)": d.mean(),
                     "t": d.mean() / d.std(ddof=1) * np.sqrt(len(d)),
                     "share under 5% owned, without": (top("no_own")["own"] < 5).mean(),
                     "share under 5% owned, with": (top("with_own")["own"] < 5).mean()})
    return pd.DataFrame(rows)


def main():
    log = lambda m: print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
    log("building panel")
    raw = panel.build()
    P = combiner.prepare(raw[raw["pool"] & raw["points"].notna()].copy())
    log("signal scan")
    sig = scan.scan(raw)
    quint = scan.flow_quintiles(raw)
    own = ownership_check(raw)
    log("walk-forward models")
    res = combiner.walk_forward(P, ["2024-25", "2025-26", "2026-27"], CANDIDATES, log=log)
    res.to_parquet(panel.OUT_DIR / "walk_forward.parquet")
    g = gate(res)
    passed = sorted([v for v in CANDIDATES if g[v]["forecast_pass"] and v not in REFERENCE_ONLY],
                    key=lambda v: -np.mean([c["d_rank"] for c in g[v]["checks"].values()]))[:2]
    verdicts, replays = {}, {}
    if passed:
        log(f"decision tests for {passed}")
        jobs = [(s, v) for v in passed for s in DECISION_SEASONS]
        with ProcessPoolExecutor(4) as ex:
            frames = list(ex.map(_same_state, jobs))
            rep_jobs = [(s, m) for m in ["base", passed[0]] for s in DECISION_SEASONS]
            rep = list(ex.map(_replay, rep_jobs))
        for v in passed:
            verdicts[v] = decision_verdict([f for (s, m), f in zip(jobs, frames) if m == v])
        replays = {job: df for job, df in zip(rep_jobs, rep)}
    write_report(sig, quint, own, res, g, verdicts, replays)
    log(f"report written: {REPORT}")


def write_report(sig, quint, own, res, g, verdicts, replays):
    f = lambda df: df.round(3).to_markdown(index=False)
    L = ["# Research lab report", "",
         f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m research.run`. Seasons 2023/24 to "
         "2026/27. Every number uses only information available at the deadline it refers to.", "",
         "## 1. Signal scan (one gameweek ahead, decision pool)", "",
         "IC: rank correlation with points. rIC: correlation with what the current model got wrong. "
         "|t| above 3 is worth testing.", "", f(sig), "",
         "### Transfer flow among players the model rates 90%+ to play", "",
         quint.round(3).to_markdown(), "",
         "Heavy selling into a deadline predicts blanks the stats cannot see (injuries, rotation). "
         "Among players the model already rates as nailed, heavy buying adds nothing: the crowd knows bad news, not good news.", "",
         "## 2. Ownership in the projection", "",
         "The old live calibration added a term for overall ownership. Out of sample:", "", f(own), "",
         "No significant gain, and it pushes the top picks toward template players. Removed from live xP "
         "(28 Nov 2026). Ownership stays in the rank layer only, as a measure of what rivals hold.", "",
         "## 3. Forecast models, walk-forward", ""]
    per = combiner.periods(res)
    for name, mask in per.items():
        r = res[mask]
        if r.empty:
            continue
        m = pd.DataFrame({v: combiner.metrics(r, "pred_" + v) for v in ["base"] + CANDIDATES}).T
        L += [f"**{name}**", "", m.round(3).to_markdown(), ""]
    L += ["## 4. Promotion gate", "", "| Candidate | Forecast gate | Decision test | Verdict |", "| --- | --- | --- | --- |"]
    for v in CANDIDATES:
        fc = "; ".join(f"{k.split(' (')[0]}: IC {c['d_rank']:+.3f} (t {c['t_rank']:.1f}), top-20 {c['d_top20']:+.2f}"
                       for k, c in g[v]["checks"].items())
        if v in verdicts:
            d = verdicts[v]
            dec = (f"{d['pts_per_gw']:+.2f} pts/GW (t {d['t']:.2f}, 90% {d['ci90'][0]:+.2f} to {d['ci90'][1]:+.2f}, "
                   f"{d['weeks']} GWs)")
            verdict = d["verdict"]
        elif v in REFERENCE_ONLY:
            dec, verdict = "not run", "REFERENCE ONLY (uses ownership levels)"
        else:
            dec, verdict = "not run", "REJECT" if not g[v]["forecast_pass"] else "not tested"
        L.append(f"| {v} | {'pass' if g[v]['forecast_pass'] else 'fail'}: {fc} | {dec} | {verdict} |")
    if verdicts:
        sd = np.mean([d["sd"] for d in verdicts.values()])
        need = lambda e: (2 * sd / e) ** 2
        L += ["", "## 5. How much evidence a decision edge needs", "",
              f"In the same-state test, the week-to-week difference between two models has a standard deviation of "
              f"{sd:.1f} points. To show an edge at t = 2 takes about {need(2):.0f} gameweeks for 2 points a week, "
              f"{need(1):.0f} for 1 point and {need(0.5):.0f} for 0.5. A season has 38. So forecast accuracy "
              "(hundreds of players every week) is the main yardstick, and decision tests are a do-no-harm check.", ""]
    if replays:
        rows = [{"season": s, "model": m, "points GW6-38": int(df["points"].sum()), "hits": int(df["hits"].sum())}
                for (s, m), df in replays.items()]
        L += ["## 6. Full-season replays (noisy, for reference)", "", pd.DataFrame(rows).to_markdown(index=False), ""]
    L += ["## Notes", "",
          "- Transfer counts and ownership per gameweek come from the vaastav archive and are taken as they stood "
          "at that gameweek's deadline. From Nov 2026 the collector records them itself (history/), which allows "
          "testing at brief time (36 hours out) rather than at the deadline.",
          "- The archive has no record of FPL's injury flags at past deadlines, so the backtest model plays without "
          "them while the live model scales next-GW xP by them. Part of the flow signal overlaps with those flags; "
          "the shadow log measures how much is left.", ""]
    REPORT.write_text("\n".join(L))


if __name__ == "__main__":
    main()
