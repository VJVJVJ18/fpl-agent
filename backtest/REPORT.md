# Backtest report

Generated 2026-11-28 12:45 UTC. Every prediction uses only data from before the gameweek it predicts. Accuracy is measured on the decision pool: players who played in the previous 4 gameweeks.

## Accuracy by period

| Period | Rank corr. 1 GW | Rank corr. 6 GW | MAE 1 GW | Bias | Top-20 avg pts | GWs |
| --- | --- | --- | --- | --- | --- | --- |
| train (2025/26 GW6-24) | 0.503 | 0.543 | 1.87 | +0.04 | 4.33 | 19 |
| valid (2025/26 GW25-38) | 0.501 | 0.555 | 1.88 | +0.06 | 4.94 | 14 |
| holdout (2026/27 GW8-11) | 0.513 | 0.545 | 1.79 | -0.05 | 5.11 | 4 |
| season to date (2026/27 GW6-11) | 0.509 | 0.568 | 1.80 | -0.01 | 4.76 | 6 |

## Against baselines (2025/26 GW6-38, one gameweek ahead)

| Method | Rank corr. | MAE | Top-20 avg pts |
| --- | --- | --- | --- |
| This model | 0.502 | 1.87 | 4.59 |
| Form (avg points, last 4 GWs) | 0.403 | 2.04 | 3.95 |

FPL's own archived xP is not used as a baseline: in the archive it is 0 for 83% of players who then played 90 minutes, so it is not the pre-deadline projection.

## Calibration (2025/26 GW6-38)

| xp      |    n |   predicted |   actual |
|:--------|-----:|------------:|---------:|
| under 2 | 5553 |        0.97 |     1.2  |
| 2 to 4  | 5518 |        3.02 |     2.81 |
| 4 to 6  | 1486 |        4.53 |     4.07 |
| 6+      |  111 |        7.38 |     6.98 |

## Calibration fitted on train, checked on valid

Fitted coefficients: {"xp_only": [0.759, 0.7196], "with_own": [0.8705, 0.6085, 0.2209]}.

| Version (valid period) | MAE 1 GW | Rank corr. 1 GW |
| --- | --- | --- |
| Raw | 1.880 | 0.501 |
| Calibrated (live) | 1.863 | 0.502 |
| Calibrated + ownership (research only, not used live) | 1.861 | 0.502 |

Calibration by projected points, valid period, calibrated (live):

| xp      |    n |   predicted |   actual |
|:--------|-----:|------------:|---------:|
| under 2 | 2202 |        1.08 |     1.19 |
| 2 to 4  | 2743 |        2.97 |     2.88 |
| 4 to 6  |  272 |        4.51 |     4.85 |
| 6+      |   43 |        7.01 |     7.42 |

## Do low-owned picks deliver? (model's top 40 each gameweek, valid period)

Ownership from FPL's counts (13.1 million managers). If low-owned picks score close to their projection, the model's differentials can be trusted.

Raw model:

| own      |   n |   predicted |   actual |
|:---------|----:|------------:|---------:|
| under 5% | 240 |        4.85 |     4.31 |
| 5 to 20% | 187 |        4.89 |     4.5  |
| over 20% | 133 |        5.44 |     5.2  |

Calibrated (live):

| own      |   n |   predicted |   actual |
|:---------|----:|------------:|---------:|
| under 5% | 240 |        4.24 |     4.34 |
| 5 to 20% | 186 |        4.27 |     4.56 |
| over 20% | 134 |        4.66 |     5.19 |

## Captaincy on Vuk's own XI (2026/27)

Model's captain scored 39 points against 33 for the captain actually picked, over GW6-11 (same choice in 5 of 6 gameweeks).

|   gw |   model |   actual | same   |
|-----:|--------:|---------:|:-------|
|    6 |       6 |        6 | True   |
|    7 |      13 |       13 | True   |
|    8 |       8 |        8 | True   |
|    9 |       2 |        2 | True   |
|   10 |       8 |        2 | False  |
|   11 |       2 |        2 | True   |

## Spread of points around the projection (for the rank-aware objective)

Fitted on train: Var(points) = 3.3891 x xP + -0.0669 x xP^2. Checked on valid:

| xpc     |    n |   actual_var |   predicted_var |
|:--------|-----:|-------------:|----------------:|
| under 1 | 1007 |         2.99 |            1.94 |
| 1-2     | 1195 |         5.65 |            4.93 |
| 2-3     | 1401 |         6.85 |            8.14 |
| 3-4     | 1342 |        10.6  |           10.84 |
| 4-5     |  227 |        13.99 |           13.38 |
| 5+      |   88 |        18.88 |           18.43 |

## Parameters

```json
{
  "min_window": 6,
  "min_decay": 0.6,
  "rate_decay": 0.97,
  "k_rate": 12.0,
  "k_prior": 16.0,
  "team_decay": 0.95,
  "k_team": 8.0,
  "home": 1.2,
  "promoted_att": 0.85,
  "promoted_def": 1.15,
  "k_dc": 3.0,
  "att_power": 0.6
}
```
