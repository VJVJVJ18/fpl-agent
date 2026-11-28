# Research log

One entry per improvement sprint: what was tested, the decisive numbers, the verdict.
Verdicts: ADDED (new shadow version), REJECTED, PARKED (promising, needs more data).

## Sprint 0, 28 Nov 2026: research lab and shadow v1

- Built the point-in-time panel (2023/24 to 2026/27), signal scan, walk-forward models and
  same-state decision tests. Full numbers: `research/REPORT.md`.
- Ownership in the live projection: no out-of-sample gain (top-11 picks -0.05 points per player
  per GW, t -0.7) and it tilted the top 40 toward template players. Removed from live xP.
- Transfer flow: among players the model rates 90%+ to play, the most-sold fifth blanked 24.7%
  of the time (8 to 15% for the rest) and scored 0.54 points below projection. The most-bought
  fifth scored exactly its projection.
- Overlay (stats + flow, learns the live model's errors): rank IC +0.040 (t 6.8) and +0.050
  (t 4.3) on the two untouched periods; same-state decisions +0.20 points per GW (90% -0.77 to
  +1.15). Verdict: ADDED as shadow v1, live from GW13.

## Sprint 1, 28 Nov 2026: bookmaker odds

Script: `research/sprints/s01_odds.py`. Pre-match market prices only (recorded near the
deadline); closing prices excluded because they carry post-deadline team news.

- Team level, goals conceded per team-match, log-likelihood (higher is better):

  | Season | Model | Market |
  | --- | --- | --- |
  | 2023/24 | -1.5607 | -1.5524 |
  | 2024/25 | -1.5141 | -1.4988 |
  | 2025/26 | -1.4633 | -1.4346 |
  | 2026/27 | -1.4248 | -1.4228 |

  The market is the better forecaster in every season.
- Player level, untouched periods, overlay + odds against v1: rank IC +0.002 (t 1.2) and -0.001
  (t -0.3); top-20 points -0.14 and -0.12. No gain: the model's xG-based team ratings are close
  enough to the market that player rankings barely move.
- Defender-only clean-sheet swap (market goals against in place of the model's): defender rank IC
  higher in 3 of 4 periods (t 1.5 to 2.0), top-5 defender points higher in 3 of 4. Below the bar.
- Verdict: REJECTED as a shadow version; defender swap PARKED (backlog item 8).
