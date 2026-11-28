# Research lab report

Generated 2026-11-28 12:47 UTC by `python -m research.run`. Seasons 2023/24 to 2026/27. Every number uses only information available at the deadline it refers to.

## 1. Signal scan (one gameweek ahead, decision pool)

IC: rank correlation with points. rIC: correlation with what the current model got wrong. |t| above 3 is worth testing.

| signal                                 |     IC |    IC t |     rIC |   rIC t |   GWs |
|:---------------------------------------|-------:|--------:|--------:|--------:|------:|
| base model xP                          |  0.516 |  88.594 | nan     | nan     |   104 |
| minutes last GW                        |  0.46  |  72.646 |   0.013 |   2.255 |   104 |
| transfers in (rank)                    |  0.452 |  88.32  |   0.084 |  19.841 |   104 |
| points per GW, last 4                  |  0.413 |  76.239 |   0.005 |   1.039 |   104 |
| BPS per GW, last 6                     |  0.393 |  60.555 |  -0.009 |  -1.81  |   104 |
| starts, last 6 GWs                     |  0.374 |  62.031 |  -0.04  |  -8.435 |   104 |
| xG+xA per GW, last 3                   |  0.349 |  63.472 |   0.031 |   5.367 |   104 |
| ownership level (rank)                 |  0.293 |  57.856 |   0.043 |  10.187 |   104 |
| FPL threat per GW, last 6              |  0.291 |  55.364 |   0.031 |   5.932 |   104 |
| defensive contributions per GW, last 6 |  0.287 |  31.567 |  -0.022 |  -2.979 |    38 |
| transfers out (rank)                   |  0.256 |  47.224 |   0.014 |   3.302 |   104 |
| price                                  |  0.239 |  36.383 |   0.058 |   7.766 |   104 |
| ownership change into the deadline     |  0.233 |  25.963 |   0.069 |  10.282 |   104 |
| net transfers (share of all flow)      |  0.204 |  22.747 |   0.072 |   9.653 |   104 |
| xG+xA per 90, last 6                   |  0.158 |  24.585 |   0.037 |   7.431 |   104 |
| price change since GW1                 |  0.131 |  22.288 |   0     |   0.063 |   104 |
| model clean-sheet probability          |  0.104 |  15.264 |   0.038 |   4.523 |   104 |
| goals+assists minus xGI, last 6        |  0.038 |   6.822 |   0.02  |   3.119 |   104 |
| goals minus xG, season                 |  0.013 |   2.127 |   0.007 |   1.057 |   104 |
| team xG conceded, last 6               | -0.076 | -12.919 |  -0.028 |  -4.441 |   104 |

### Transfer flow among players the model rates 90%+ to play

| q           |   players |   projected |   scored |   blank_rate |   scored minus projected |
|:------------|----------:|------------:|---------:|-------------:|-------------------------:|
| most sold   |      4175 |       2.918 |    2.383 |        0.247 |                   -0.535 |
| 2           |      4175 |       2.753 |    2.606 |        0.154 |                   -0.147 |
| 3           |      4174 |       2.8   |    2.696 |        0.151 |                   -0.104 |
| 4           |      4175 |       3.258 |    3.202 |        0.104 |                   -0.056 |
| most bought |      4175 |       3.577 |    3.581 |        0.082 |                    0.004 |

Heavy selling into a deadline predicts blanks the stats cannot see (injuries, rotation). Among players the model already rates as nailed, heavy buying adds nothing: the crowd knows bad news, not good news.

## 2. Ownership in the projection

The old live calibration added a term for overall ownership. Out of sample:

|   top k by xP |   with minus without ownership (pts/player/GW) |      t |   share under 5% owned, without |   share under 5% owned, with |
|--------------:|-----------------------------------------------:|-------:|--------------------------------:|-----------------------------:|
|            11 |                                         -0.051 | -0.674 |                           0.145 |                        0.051 |
|            20 |                                          0.045 |  1.042 |                           0.224 |                        0.104 |
|            40 |                                          0.053 |  1.728 |                           0.338 |                        0.206 |

No significant gain, and it pushes the top picks toward template players. Removed from live xP (28 Nov 2026). Ownership stays in the rank layer only, as a measure of what rivals hold.

## 3. Forecast models, walk-forward

**2024-25 (all)**

|            |   rank_1gw |   rank_6gw |   mae_1gw |   top20_pts |   top1_pts |   gws |
|:-----------|-----------:|-----------:|----------:|------------:|-----------:|------:|
| base       |      0.524 |      0.607 |     1.728 |       4.947 |      8.848 |    33 |
| stats      |      0.519 |      0.627 |     1.721 |       4.898 |      6.697 |    33 |
| full       |      0.54  |      0.636 |     1.701 |       5.086 |      7.182 |    33 |
| stats_flow |      0.542 |      0.64  |     1.7   |       4.924 |      7.515 |    33 |
| overlay    |      0.543 |      0.638 |     1.702 |       5.027 |      8.848 |    33 |

**2025-26 GW6-24 (base tuned here)**

|            |   rank_1gw |   rank_6gw |   mae_1gw |   top20_pts |   top1_pts |   gws |
|:-----------|-----------:|-----------:|----------:|------------:|-----------:|------:|
| base       |      0.503 |      0.543 |     1.868 |       4.329 |      6.053 |    19 |
| stats      |      0.501 |      0.563 |     1.86  |       4.297 |      6.368 |    19 |
| full       |      0.536 |      0.586 |     1.836 |       4.545 |      6.895 |    19 |
| stats_flow |      0.537 |      0.585 |     1.833 |       4.447 |      6.947 |    19 |
| overlay    |      0.533 |      0.58  |     1.841 |       4.537 |      5.895 |    19 |

**2025-26 GW25-38 (untouched)**

|            |   rank_1gw |   rank_6gw |   mae_1gw |   top20_pts |   top1_pts |   gws |
|:-----------|-----------:|-----------:|----------:|------------:|-----------:|------:|
| base       |      0.501 |      0.555 |     1.88  |       4.936 |      7.5   |    14 |
| stats      |      0.516 |      0.605 |     1.852 |       5.089 |      7.214 |    14 |
| full       |      0.539 |      0.617 |     1.846 |       5.146 |      7.929 |    14 |
| stats_flow |      0.542 |      0.62  |     1.842 |       5.179 |      7.357 |    14 |
| overlay    |      0.541 |      0.614 |     1.851 |       5.232 |      8.214 |    14 |

**2026-27 GW6-10 (untouched)**

|            |   rank_1gw |   rank_6gw |   mae_1gw |   top20_pts |   top1_pts |   gws |
|:-----------|-----------:|-----------:|----------:|------------:|-----------:|------:|
| base       |      0.52  |      0.568 |     1.768 |        4.73 |        8.2 |     5 |
| stats      |      0.534 |      0.581 |     1.741 |        4.74 |        8.4 |     5 |
| full       |      0.569 |      0.602 |     1.68  |        5.4  |        8.4 |     5 |
| stats_flow |      0.569 |      0.607 |     1.683 |        5.28 |        6.4 |     5 |
| overlay    |      0.57  |      0.598 |     1.677 |        5.46 |        8.4 |     5 |

## 4. Promotion gate

| Candidate | Forecast gate | Decision test | Verdict |
| --- | --- | --- | --- |
| stats | fail: 2025-26 GW25-38: IC +0.015 (t 2.8), top-20 +0.15; 2026-27 GW6-10: IC +0.014 (t 2.3), top-20 +0.01 | not run | REJECT |
| full | pass: 2025-26 GW25-38: IC +0.038 (t 6.1), top-20 +0.21; 2026-27 GW6-10: IC +0.049 (t 3.9), top-20 +0.67 | not run | REFERENCE ONLY (uses ownership levels) |
| stats_flow | pass: 2025-26 GW25-38: IC +0.041 (t 6.8), top-20 +0.24; 2026-27 GW6-10: IC +0.049 (t 4.0), top-20 +0.55 | -0.53 pts/GW (t -0.80, 90% -1.59 to +0.51, 64 GWs) | REJECT |
| overlay | pass: 2025-26 GW25-38: IC +0.040 (t 6.8), top-20 +0.30; 2026-27 GW6-10: IC +0.050 (t 4.3), top-20 +0.73 | +0.20 pts/GW (t 0.33, 90% -0.77 to +1.15, 64 GWs) | SHADOW |

## 5. How much evidence a decision edge needs

In the same-state test, the week-to-week difference between two models has a standard deviation of 5.0 points. To show an edge at t = 2 takes about 25 gameweeks for 2 points a week, 102 for 1 point and 407 for 0.5. A season has 38. So forecast accuracy (hundreds of players every week) is the main yardstick, and decision tests are a do-no-harm check.

## 6. Full-season replays (noisy, for reference)

| season   | model      |   points GW6-38 |   hits |
|:---------|:-----------|----------------:|-------:|
| 2024-25  | base       |            2016 |     24 |
| 2025-26  | base       |            1830 |     28 |
| 2024-25  | stats_flow |            1957 |     40 |
| 2025-26  | stats_flow |            1997 |     12 |

## Notes

- Transfer counts and ownership per gameweek come from the vaastav archive and are taken as they stood at that gameweek's deadline. From Nov 2026 the collector records them itself (history/), which allows testing at brief time (36 hours out) rather than at the deadline.
- The archive has no record of FPL's injury flags at past deadlines, so the backtest model plays without them while the live model scales next-GW xP by them. Part of the flow signal overlaps with those flags; the shadow log measures how much is left.
