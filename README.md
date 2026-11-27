# fpl-agent

Data and model for the "Ekipa Nervno" FPL analyst. Advice only: nothing here logs in to FPL or makes transfers.

## What runs where

| Part | Where | What it does |
| --- | --- | --- |
| `collector/collect.py` | GitHub Actions, every 3 hours | Saves public FPL data to the `data` branch, plus the 10,000th-place score each run and a 1,000-team top-10k ownership sample each gameweek |
| `fpl/squad.py` | Each brief | Rebuilds the squad, selling prices, bank, free transfers (estimate) and chips left |
| `fpl/data.py`, `fpl/model.py`, `fpl/scoring.py` | Each brief | Loads match data; projects expected points (minutes, xG/xA rates, team strength, clean sheets, DefCon, bonus); scoring read from FPL's own config |
| `fpl/solver.py` | Each brief | Optimises transfers, XI, captain and bench over 6 gameweeks, with one fixed starting goalkeeper (no rotation); values Bench Boost and Free Hit by gameweek |
| `fpl/rank.py` | Each brief | Rank-aware objective: from the gap to rank 10,000 and the top 10k's ownership, how many expected points a differential is worth |
| `fpl/brief_data.py` | Each brief | Runs all of the above and writes `brief_data.json` |
| `fpl/gate.py` | Every morning | SKIP, REVIEW or BRIEF (deadline within 36 hours) |
| `fpl/review.py`, `fpl/backtest.py` | After each gameweek | Scores the projections, keeps the track record, re-tunes the model when it helps out of sample |
| `BRIEF.md` | Vuk's Claude chat | The rulebook every brief follows |

The `data` branch is replaced on each run, so the repo stays small. It holds only data anyone can see on the FPL site. Plans and recommendations are never stored here.

## Settings

`config.json` holds the FPL team ID and the expected team name. If the name doesn't match, the run fails and GitHub emails you.

## Run it by hand

GitHub > Actions > collect > Run workflow. Then check the squad:

```
python -m fpl.squad https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data
```

## Backtest

`python -m fpl.backtest` writes `backtest/REPORT.md`. `--tune` re-tunes the settings and keeps them only if out-of-sample accuracy does not drop.

## Tests

```
python -m unittest discover tests
```
