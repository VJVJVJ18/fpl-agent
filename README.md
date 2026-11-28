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
| `fpl/shadow.py` | Each brief | Shadow book: up to 3 frozen candidate models, logged next to the live one and scored at each review; never used for decisions until one passes the live gate and Vuk agrees (decision at the GW18 review) |
| `research/` | After every review | Research lab and weekly improvement sprints: point-in-time panel, signal scan, walk-forward models, decision tests with the real optimiser, promotion gate. Backlog in `research/BACKLOG.md`, results in `research/LOG.md` |
| `BRIEF.md` | Vuk's Claude chat | The rulebook every brief follows |

The `data` branch is replaced on each run, so the repo stays small, but its files carry over from run to run. It holds only public data: FPL's own data, plus bookmaker odds from football-data.co.uk. Plans and recommendations are never stored here.

`history/<season>/` on the `data` branch is the point-in-time record that no public archive keeps. It holds:
- every player's injury flag, chance of playing, price, ownership and transfer counts, every run in the 48 hours before a deadline and daily otherwise;
- a log of every news change;
- the upcoming matches' odds, each time they move.

It is never deleted. The research lab needs it to test signals at brief time rather than at the deadline.

## Settings

`config.json` holds the FPL team ID and the expected team name. If the name doesn't match, the run fails and GitHub emails you.

## Run it by hand

GitHub > Actions > collect > Run workflow. Then check the squad:

```
python -m fpl.squad https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data
```

## Backtest

`python -m fpl.backtest` writes `backtest/REPORT.md`. `--tune` re-tunes the settings and keeps them only if out-of-sample accuracy does not drop.

## Research lab

```
pip install -r research/requirements.txt
python -m research.run      # about 20 minutes; writes research/REPORT.md
```

## Tests

```
python -m unittest discover tests
```
