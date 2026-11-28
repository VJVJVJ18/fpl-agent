# Research backlog

Ranked by expected gain times data readiness. Each improvement sprint (BRIEF.md section 7) takes the
highest item whose data is ready, tests it, and records the result in `research/LOG.md`.
Rules for every item: no ownership levels as inputs; never change an existing panel column (add new
ones); a new shadow version only if it passes the research gate on both untouched periods.

| # | Hypothesis | Why it might be alpha | Test | Data ready |
| --- | --- | --- | --- | --- |
| 1 | The decision settings are mis-set: free-transfer value (1.5 points, never tested) and the hit rule | In the season replays hits cost 12 to 56 points a season; banking and hit choices compound over many weeks | Same-state decision tests over a 6-GW scoring window, comparing FT value 0.5 / 1.0 / 1.5 / 2.5 and hit bars 4 / 6 / 8. Score decisions on realized points and, to cut noise, on the shadow model's walk-forward xP as an independent referee | Now |
| 2 | Rotation after European and cup matches is predictable | The model only sees league minutes; managers rotate after midweek games, and minutes multiply every other term | Add all-competition minutes and days of rest (olbauday/FPL-Core-Insights on GitHub, 2025/26 on) as new panel columns; walk-forward vs v1 | Now |
| 3 | Top-end calibration: learned models flatten the top, which hurts captaincy | v1 keeps the structural spread by learning residuals, but captain picks still differ | Captain-only metrics (top-1 and top-3 points per GW) for v1 vs live; a quantile or tail-weighted residual model as a new version | Now |
| 4 | Transfer flow adds nothing once FPL's own injury flags are known | If true, v1's flagged column double-counts, and a version with flags as inputs is cleaner | Point-in-time flags and flow from `history/` (collector, from 28 Nov 2026): does flow at brief time predict blanks among players with no flag? | About GW16 (needs 4 or more gameweeks of history) |
| 5 | Flow at brief time (36 hours out) is weaker than flow at the deadline | The backtest measured flow at the deadline; the brief sees it earlier | Same history files: rank correlation of blank prediction at each snapshot time | About GW16 |
| 6 | FPL's starting price is an unbiased prior the model underweights | Price had residual IC +0.058 (t 7.8). The starting price is FPL's own valuation, while price changes come from crowd transfers | New column: starting price only (now_cost minus cost_change_start); new version vs v1 | Now |
| 7 | Set-piece and penalty duty changes are underpriced | A new penalty taker is among the most valuable pieces of news | History of penalties and set-piece order (collector records it from 28 Nov 2026); event study on points after a change | Season end (few events) |
| 8 | Bookmaker odds, revisited | Sprint 1 (28 Nov) rejected them for player rankings, but the defender-only clean-sheet swap was positive in 3 of 4 periods (t up to 2.0) | Re-run `research/sprints/s01_odds.py` with more data; defender and keeper rankings only; brief-time odds from `history/` | GW19 |
