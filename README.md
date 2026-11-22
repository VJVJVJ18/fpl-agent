# fpl-agent

Data and model for the "Ekipa Nervno" FPL analyst. Advice only: nothing here logs in to FPL or makes transfers.

## What runs where

| Part | Where | What it does |
| --- | --- | --- |
| `collector/collect.py` | GitHub Actions, every 3 hours plus before each brief | Saves public FPL data (game data, fixtures, per-gameweek stats, this team's picks, transfers and history) to the `data` branch |
| `fpl/squad.py` | Claude's scheduled task | Rebuilds the squad, selling prices, bank, free transfers (estimate) and chips left from that data |
| Model and solver | Claude's scheduled task (Phase 2) | Projects points and finds the best moves |

The `data` branch is replaced on each run, so the repo stays small. It holds only data anyone can see on the FPL site. Plans and recommendations are never stored here.

## Settings

`config.json` holds the FPL team ID and the expected team name. If the name doesn't match, the run fails and GitHub emails you.

## Run it by hand

GitHub > Actions > collect > Run workflow. Then check the squad:

```
python -m fpl.squad https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data
```

## Tests

```
python -m unittest discover tests
```
