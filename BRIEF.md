# FPL brief rulebook: Ekipa Nervno

Everything FPL runs in Vuk's single Claude chat. One scheduled message arrives every morning at 07:46 UK ("FPL daily check"); this file says what to do with it. Changes to how the brief works are made here, as commits, so every later run picks them up.

## 1. Vuk's approach (applies to every decision)

- **Data first, by far.** Every recommendation rests on numbers, with the decisive number stated. Opinion pieces are never the reason for a move.
- **Not consensus.** Template moves are fine when the data backs them, never because "everyone is doing it". Top-10k ownership (EO) is a risk measure and a way to spot where the market is wrong, not evidence of points.
- **Data-backed differentials are welcome.** If the numbers support a low-owned player or an unusual move, recommend it and size the risk. Vuk is happy to take those risks.
- **Fixtures are central.** Targets come from fixture runs over the next 6 gameweeks, rated by opponent strength (model team ratings, betting odds), not FPL's difficulty rating.
- **Chips are planned weeks ahead.** Never recommend a chip for this gameweek without comparing it with every other gameweek it could be played in.
- **Hits:** a -4 only if the extra transfer gains 6+ projected points over the next 3 gameweeks, arithmetic shown.
- **Always hunt for edge by deviating from the template** (Vuk, 27 Nov 2026). Calculated risks, not deviation for its own sake: every deviation must be data-backed, and its size is set by the rank maths below.
- **Goal:** top 10k overall. The optimiser plays for rank, not points (`fpl/rank.py`):
  - It knows the gap to the manager at rank 10,000 (collector, every run) and who the top 10k own and captain (a 1,000-team sample each gameweek).
  - Behind the target, spread against the top 10k is worth points: a low-owned starter or captain earns a bonus, a template one a penalty, both sized by kappa = gap / (2 x gameweeks left x weekly spread²).
  - Ahead of it, the signs flip and the template is preferred. As the gap closes, the plan converges on pure expected points.
  - A player who won't play earns nothing for being low-owned: the bonus scales with the spread of his points, which is 0 at 0 xP.
- **No goalkeeper rotation, ever** (Vuk, 27 Nov 2026). One starting keeper plays every gameweek he's available; the backup only covers an injury or ban, and plays in a Bench Boost week. The optimiser enforces this (`starting_gk` in each plan). Never recommend a second keeper to rotate by fixture, and never frame a keeper transfer that way.
- **Advice only.** Never log in to FPL or make transfers.

## 2. The daily check

1. **Book tomorrow first:**
   - Use `send_later` at 07:46 Europe/London tomorrow, named "FPL daily check", with the same message text as today's.
   - Skip this only if `list_triggers` already shows an enabled one for tomorrow.
   - This chain is the only schedule. Never create other schedules or new chats.
2. `cd /home/claude/fpl-agent && git pull`. Clone the repo there if it's missing.
3. Install the packages if missing: `pip install -q --break-system-packages -r requirements.txt`.
4. Run `python3 -m fpl.gate`. Then act on each line it prints:
   - **SKIP**: reply with that one line only. No notification, no other work.
   - **STALE**:
     - Refresh the collector: `gh workflow run collect.yml -R VJVJVJ18/fpl-agent`, then `gh run watch`.
     - Run the gate again.
     - If the refresh fails, carry on with the old data and say how old it is.
   - **REVIEW GW n**: see section 7. It runs silently; its summary goes into the next brief.
   - **BRIEF GW n (first brief)**: run the full brief (sections 3 to 5).
   - **BRIEF GW n (update)**: rerun the full analysis with fresh data and news. Then lead with a "Since yesterday" line: what changed and whether the call moved. If nothing material changed, say "No change: yesterday's Option A stands" and keep the exec summary table.

   With a 07:46 check and a 36-hour window, every deadline gets two briefs: the morning of the day before, and the morning of deadline day.
5. **Deliver** every brief with `SendUserMessage`, then `PushNotification`. The notification is one line under 200 characters, leading with the call, for example: "GW12: Palmer(IPS)->Verbruggen, C Haaland, no chip. Deadline Fri 18:30. 1 edge flagged". If a delivery tool is missing or fails, say so at the top of the brief.

## 3. Data (one command does the heavy lifting)

- `python3 -m fpl.brief_data --save` writes `brief_data.json` and logs this week's projections for the review. It contains:
  - **Squad:** rebuilt from FPL (selling prices, bank, free-transfer estimate, chips left, rank).
  - **Projections:** points per player for the next 6 gameweeks, calibrated, with availability applied to the next gameweek.
  - **Transfer plans** for 0 up to free transfers + 1: optimised transfers, bank after, projected points by gameweek, 1-gameweek and 3-gameweek gains after hits, captain, vice and bench order.
  - **Captain table and chip values:** Bench Boost points and Free Hit gain per gameweek, to GW19 in the first half.
  - **Fixture ratings:** attack ease and clean-sheet odds per team over 6 gameweeks.
  - **Edge flags:** the data side of the edge scan, with owned players marked.
  - **Rank objective:** the gap to rank 10,000, gameweeks left, kappa, and what it means in points (captain and slot bands).
  - **Rank plans** (Option A by default) next to **points-only plans** (Option B): each with the template count (starters 60%+ owned in the top 10k) and the XI's top-10k ownership.
  - **Deviation candidates:** for each template starter, low-owned alternatives (under 30% top-10k, nailed) whose rank-adjusted 6-GW value beats his, with the xP cost.
- **Commit** `predictions/` and push. Brief runs may commit data files (`predictions/`, `reviews/`, `params.json`, `backtest/REPORT.md`), never code. Code changes only when Vuk asks.
- **Web sources (Claude):**
  - Press conferences and team news: premierleague.com, club sites, BBC, Sky, The Athletic.
  - Betting odds: match odds, goal totals, anytime scorer, named and dated.
  - Top-10k ownership and EO come from the collector's own sample (`top10k/<gw>.json`, the previous gameweek's teams). If it's missing, use LiveFPL or fpl.team, then Fantasy Football Scout's top-10k write-ups.
  - Opinion sites only for a specific, dated argument, labelled as opinion.
- **Plan:** read `/areas/fpl.md` in memory (chip plan, agreed moves). If the data contradicts it, for example a chip already used, the data wins; say so.
- **Source rules:**
  - No fact without a source read in this run.
  - Missing means "Not found", never a guess.
  - One xG provider (FPL's Opta-derived xG via the collector).
  - Never use FBref for advanced stats.
  - Predictions (line-ups, price changes) are labelled as such.

## 4. Judgement on top of the model

- **Trust the model where the backtest supports it.**
  - Its ranking beats a form baseline clearly: rank correlation about 0.50 against 0.40 one gameweek ahead, and its top-20 picks averaged 4.6 points against 4.0.
  - It holds up on this season's data.
  - Calibrated projections are slightly conservative at the top end.
- **Minutes are the model's blind spot.** It learns minutes from recent matches only. Adjust every player you recommend using press conferences and news: injuries, rotation, Europe, returns. Say when you overrode the model and why.
- **Differentials need one extra check.** In the backtest, low-owned top picks delivered about 89 to 92% of their raw projection, against 96% for template picks. Before recommending one, confirm:
  - it's nailed (projected 60+ minutes at least 75% of the time, and no news against it);
  - the fixtures and the odds support it.
- **Edge scan.** From the data flags plus web checks, keep the 1 to 3 strongest. Each needs:
  - the number that makes the case and the number that would kill it;
  - top-10k EO;
  - whether it's already consensus.

  Web-only detectors to add:
  - odds against ownership;
  - new penalty or set-piece takers announced in press conferences;
  - fixture swings the crowd hasn't reacted to.
- **Transfers:**
  - Start from the optimiser's plans. Check each "in" player's news and odds. Apply the hit rule using `gain_3gw_after_hits`.
  - Rolling has value: the optimiser counts 1.5 points per free transfer carried, an assumption not yet backtested.
- **Captain:** the highest rank-adjusted value (xP plus rank bonus, `rank_cap_value` in the captain table) after news adjustments. A differential captain must still pass the nailed check below.
- **Deviation scan (every brief):** from `deviation_candidates`, keep the 1 to 3 strongest swaps of a template starter for a low-owned one. Each needs the xP cost, the ownership swing and the reason in the data (fixtures, odds, finishing luck, set pieces). At least one goes in the brief as an edge or as Option A, unless none passes the nailed and odds checks.
- **Bench:** the optimiser's order, adjusted for news. The first sub must be nailed. The backup keeper is cover only: a cheap keeper who plays (so a Bench Boost isn't wasted) beats a non-playing one only if the price difference is small.
- **Chips:**
  - Use the chip values to GW19 (Bench Boost = bench points; Free Hit = gain over your XI). Add blanks and doubles from news.
  - Recommend a roadmap through GW19, plus a first view of the second half.
  - First-half chips are lost after the GW19 deadline.
- **Option A and Option B:**
  - A is the recommendation: the rank plan, after news and the checks above.
  - B is the points-only plan, so the cost of deviating is visible. If the two plans are the same, B is A plus the strongest deviation candidate.
  - Override the rank plan only with a reason from news or odds, and say so.
  - Both must be legal and affordable.

## 5. Output format: full brief (exactly this, nothing more)

Title line: `GW{n} brief | deadline Day DD Mon HH:MM UK | data as of HH:MM`. Add "(update)" for the second brief.

**1. Exec summary**

| | Option A (recommended) | Option B |
| --- | --- | --- |
| Transfers | out -> in, selling and buying price; hits | |
| Bank after | | |
| Captain / vice | | |
| Bench order | 1st, 2nd, 3rd sub; GK2 | |
| Chip | play or hold (and which) | |
| Projected points | GW n, and GW n to n+5 | |
| Risk | template count (XI starters 60%+ owned in the top 10k); top-10k ownership of the key players | |

**2. Why** (3 to 5 bullets): the decisive numbers and fixtures behind Option A, and the trade-off against B. Not the full analysis. One bullet is the rank maths: the gap to rank 10,000 and what it makes worth trading (captain and slot bands).

**3. Edges the market is missing** (1 to 3): player, price, top-10k EO, the data point, what would kill it, and "act now" or "watch". If nothing is strong, say "No strong edge this week".

**4. Chip roadmap:** a table from GW n to GW19 (GW, plan, why, what would change it), then one line on the second half.

**5. Before the deadline:**
- A one-line plan check.
- Up to 3 things that could change the call.
- The latest model review line, if a review ran since the last brief.

**6. Squad check:** one line saying the rebuilt squad and free-transfer estimate. Then: "Reply 'confirmed', or tell me any transfer you've made since the last deadline, and I'll redo the moves."

Rules:
- Short enough to read on a phone in about a minute.
- Source tags only on the key numbers, as [Source, DD Mon].
- Links in a short Sources list at the end.
- British English, prices as £x.xm, times in UK time.
- No em dashes, no filler.
- Name players with their club where names clash, for example Palmer (CHE) and Palmer (IPS).

## 6. After Vuk replies

- When he confirms moves, chips or plan changes, update `/areas/fpl.md`: squad, bank, free transfers, chips and plan. Read the file first.
- If he asks why, give the underlying analysis then.
- If he made transfers the data can't see yet, rerun the optimiser with his new squad and redo the exec summary.

## 7. Review and improvement loop

- **When:** the gate prints REVIEW once FPL marks a gameweek's data final. Run `python3 -m fpl.review N`.
- **What it does:**
  - Scores that gameweek's logged projections against actual points: rank correlation, error, bias, the model's top 20, and Vuk's XI and captain.
  - Appends the result to `reviews/2026-27.csv`, the season-long track record.
  - Every 3rd review, re-tunes the model (`python -m fpl.backtest --tune`), which also refits the spread of points around the projection used by the rank objective. Tuning uses last season plus this season up to 4 gameweeks ago. New settings are kept only if they do at least as well on the 4 most recent gameweeks and on last season's held-out period.
- **After it runs:** commit `reviews/`, `params.json` and `backtest/REPORT.md`, push, and carry the one-line summary into the next brief.
- **Rule changes:** changes to the rules in this file, as opposed to the model's settings, are proposed to Vuk with the evidence and made only when he agrees.

## 8. Checks before sending

- Every number in the summary has a source from this run.
- Both options are legal and affordable (the optimiser guarantees this; recheck any manual change).
- The deadline is right and in UK time.
- Nothing in the brief contradicts another part of it.
- Option B is genuinely different from A.
- The chip roadmap covers every remaining chip.
