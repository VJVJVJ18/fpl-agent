# FPL brief rulebook: Ekipa Nervno

Every scheduled FPL run in Vuk's chat follows this file. Read it in full at the start of each run (`git -C /home/claude/fpl-agent pull`, or clone the repo if it is missing). Changes to how the brief works are made here, as commits, so every future run picks them up.

## 1. Vuk's approach (applies to every decision)

- **Data first, by far.** Every recommendation rests on numbers from the data, with the decisive number stated. Opinion pieces are never the reason for a move.
- **Not consensus.** Template moves are fine when the data backs them, but never because "everyone is doing it". Top-10k ownership (EO) is a risk measure and a way to spot where the market is wrong, not evidence of points.
- **Data-backed differentials are welcome.** If the numbers support a low-owned player or an unusual move, recommend it and size the risk. Vuk is happy to take those risks.
- **Fixtures are central.** Transfer targets come from fixture runs over the next 6 gameweeks, rated by opponent strength (xG for and against, betting odds), not FPL's difficulty rating.
- **Chips are planned weeks ahead.** Never recommend a chip for this gameweek without comparing it with every other gameweek it could be played in.
- **Hits:** a -4 only if the extra transfer gains 6+ projected points over the next 3 gameweeks, arithmetic shown.
- **Goal:** top 10k overall. If his current rank is outside 10,000, lean towards differentials on close calls (chase); if inside, lean towards the template on close calls (protect).
- **Advice only.** Never log in to FPL or make transfers. Brief runs read the repo; they don't change code.

## 2. When runs happen

- **Full brief:** 18:55 UK the evening before each deadline.
- **Final check:** about 3 hours before each deadline, after the last press conferences.
- Both arrive as scheduled messages in this chat (`send_later`).
- **Schedule upkeep, at the end of every full brief:**
  1. Read the live deadlines from `bootstrap.json`.
  2. With `list_triggers`, check that this gameweek's final check and the next two gameweeks' briefs and final checks are scheduled at the right times.
  3. Create any that are missing. If a deadline has moved, delete the wrong one and create the correct one.
  4. Name them "FPL GW{n} brief" and "FPL GW{n} final check".
- **Notify Vuk at the end of every run** with `PushNotification`, one line under 200 characters, leading with the call. For example: "GW12: Palmer->Petrovic, C Haaland, no chip. Deadline Fri 18:30. 1 differential flagged". For a final check: "GW12 final check: call stands" or "GW12 final check: CHANGE, Konsa out (injured)".

## 3. Data

1. **Refresh first.** Run `gh workflow run collect.yml -R VJVJVJ18/fpl-agent`, then `gh run watch` (it takes about a minute). If that fails, use the last snapshot and say how old it is.
2. **Collector files** are under `https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data/`:
   - `meta.json`
   - `bootstrap.json` (the FPL API's bootstrap-static)
   - `fixtures.json`
   - `live/{gw}.json` (every player's stats per gameweek)
   - `entry.json`, `history.json`, `transfers.json`, `picks/{gw}.json` (Vuk's team)

   Read them with curl and Python, not WebFetch. The FPL API itself is blocked from this environment.
3. **Squad:** `python3 -m fpl.squad https://raw.githubusercontent.com/VJVJVJ18/fpl-agent/data --json`. It gives the 15 players with selling prices, bank, rank, chips left, a free-transfer estimate and legality checks. Use its selling prices; never recompute them by hand.
4. **Plan:** read `/areas/fpl.md` in memory (chip plan, agreed moves). If the data contradicts it, for example a chip already used, the data wins; say so.
5. **Web sources:**
   - Press conferences and team news: premierleague.com, club sites, BBC, Sky, The Athletic.
   - Betting odds: match odds, goal totals and anytime scorer, named and dated.
   - LiveFPL for top-10k EO, with Fantasy Football Scout's top-10k write-ups as the fallback.
   - Opinion sites only for a specific, dated argument, labelled as opinion.
6. **Source rules:**
   - No fact without a source read in this run.
   - Missing means "Not found", never a guess.
   - One xG provider per comparison (default: FPL's Opta-derived xG via the collector).
   - Never use FBref for advanced stats.
   - Predicted line-ups and price changes are labelled as predictions.

## 4. Analysis (do all of it; show only what section 6 asks for)

A. **Fixtures, GW n to n+5:**
   - Rate every team's attack and defence from xG for and against (last 6 matches, plus the season).
   - Add odds for GW n.
   - Flag blanks, doubles, and any cup or European rescheduling that could create them.
   - Note which teams' fixture runs turn good or bad, and when.

B. **Projections:**
   - If `model/` exists in the repo, run it.
   - Otherwise estimate expected points per player per gameweek with the formula in the proposal doc: minutes × attacking rates × fixture strength, plus clean sheets, DefCon and bonus.
   - Use FPL's `ep_next` as a cross-check, never as the only basis.
   - Label all projections with their method.

C. **Minutes:** chance of starting, and hook risk (starts, but off before 60), for every player considered.

D. **Edge scan.** This is the differential hunt; run every detector and keep the 1 to 3 strongest findings.
   1. **Under-owned output:** nailed starters (75%+ of minutes) in the top 15 of their position for xGI per 90 or projected points over 6 gameweeks, with top-10k EO under 10%.
   2. **Fixture swing before the crowd:** a team whose next 4 to 6 fixtures rank top 5 by opponent strength while its assets are under-owned.
   3. **Role change:** a new penalty or set-piece taker (bootstrap `penalties_order` and corners order), a position reclassification, or a move further forward.
   4. **Finishing luck:** nailed attackers with goals well below xG (buy low), and highly owned players well above it (sell-high risk).
   5. **Odds against ownership:** anytime-scorer or clean-sheet probabilities high relative to ownership.
   6. **Defensive value:** defenders and midfielders with a high DefCon hit rate and good clean-sheet odds, at low ownership.
   7. **Minutes regained:** players back from injury or rotation who have started the last 2 or more matches, before the market reacts.
   8. **Template sell signals:** highly owned players whose underlying numbers or fixtures have turned.

   Each finding needs: the number that makes the case, the number that would kill it, top-10k EO, and whether it is already consensus (yes or no).

E. **Transfers:**
   - Compare rolling, using the free transfers, and hit options by projected points over GW n to n+5, with later weeks discounted.
   - Check legality: positions, 3 per club, and budget at selling prices.
   - Apply the hit rule.

F. **Captain:** the highest projection. EO breaks only near-ties (within about 0.5 points), using the chase or protect posture.

G. **Bench:** order by the chance a starter misses × the bench player's projection. The first sub must be nailed.

H. **Chip roadmap:**
   - For every remaining chip, value each eligible gameweek:
     - Bench Boost: the bench's projected points that week, needing four nailed bench players with decent fixtures.
     - Free Hit: the best Free Hit XI minus Vuk's own XI, which is highest in blanks, doubles or weeks when many of his players face hard fixtures.
     - Wildcard, second half: timed before fixture swings or doubles.
     - Triple Captain: a double gameweek, or the single best captain fixture.
   - Recommend a plan through GW19 and a first view of the second half (GW20 onwards), with what would change it.
   - First-half chips are lost after the GW19 deadline.

I. **Plan check:** compare with `/areas/fpl.md` and say whether each recommendation follows the saved plan or deviates from it, and why.

## 5. The two options

- **Option A** is the recommendation: the highest projected points over GW n to n+5 at a risk level that fits the chase or protect posture. It may well be the differential route.
- **Option B** must be a real alternative, not a near-copy. If A is the safer or template route, B is the best data-backed differential route; if A is the differential, B is the safer route.
- Both are fully specified and legal.

## 6. Output format: full brief (exactly this, nothing more)

Title line: `GW{n} brief | deadline Day DD Mon HH:MM UK | data as of HH:MM`

**1. Exec summary**

| | Option A (recommended) | Option B |
| --- | --- | --- |
| Transfers | out -> in, selling and buying price; hits | |
| Bank after | | |
| Captain / vice | | |
| Bench order | 1st, 2nd, 3rd sub; GK2 | |
| Chip | play or hold (and which) | |
| Projected points | GW n, and GW n to n+5 | |
| Risk | template or differential; top-10k EO of the key players | |

**2. Why** (3 to 5 bullets): the decisive numbers and fixtures behind Option A, and the trade-off against B. No full analysis.

**3. Edges the market is missing** (1 to 3): player, price, top-10k EO, the data point, what would kill it, and "act now" or "watch". If the scan finds nothing strong, say "No strong edge this week" rather than forcing one.

**4. Chip roadmap:** a table from GW n to GW19 (GW, plan, why, what would change it), then one line on the second half.

**5. Before the deadline:** a one-line plan check; up to 3 things that could change the call; when the final check arrives.

**6. Squad check:** one line saying the rebuilt squad and free-transfer estimate. Then: "Reply 'confirmed', or tell me any transfer you've made since the last deadline, and I'll redo the moves."

Rules:
- Short enough to read on a phone in about a minute.
- Source tags only on the key numbers, as [Source, DD Mon].
- Links in a short Sources list at the end.
- British English, prices as £x.xm, times in UK time.
- No em dashes, no filler.

## 7. Output format: final check

Title line: `GW{n} final check | deadline HH:MM UK`

- **Verdict:** "Call stands: Option A", or "Change: ..." with the new exec-summary row(s).
- **Up to 5 bullets** covering what changed since the brief: injuries, line-up leaks, press conferences, price changes. Each one with source and time, and labelled non-start or hook risk where relevant.
- If nothing material changed: "No material changes. Option A stands."

## 8. After Vuk replies

- When he confirms moves, chips or plan changes, update `/areas/fpl.md`: squad, bank, free transfers, chips and plan. Read the file first.
- If he asks why, give the underlying analysis then.
- If he made transfers the data can't see yet, redo the exec summary with them.

## 9. Checks before sending

- Every number in the summary has a source from this run.
- Both options are legal and affordable; bank arithmetic shown in the working.
- The deadline is right and in UK time.
- Nothing in the brief contradicts another part of it.
- Option B is genuinely different from A.
- The chip roadmap covers every remaining chip.
