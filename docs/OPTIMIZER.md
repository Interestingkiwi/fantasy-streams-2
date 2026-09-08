# The daily lineup optimizer

Design notes for MIGRATION Phase 3 item 4 — porting the roster optimizer out of
the old repo (`Interestingkiwi/fantasy-streams`, `app.py:388 get_optimal_lineup`).

Written before the port, deliberately: several of the decisions below are much
cheaper to make now than to retrofit.

**Status.** Steps 1-5 and step 6's Tier 3 are built and tested:
`lineup_utils.py` (the matcher), `daily_value.py` (per-game category values),
`goalie_starts.py` (start probabilities), `matchup_weights.py` (category
weighting), `opponent_strength.py` + `scrape_team_stats.py` (who the player is
facing) and `manager_profiles.py` (opponent add/drop style). Nothing calls them
yet - that needs the Phase 2 ETL for real rosters.

Only step 6's Tiers 1 and 2 remain, and they wait on the ETL for live free
agents and rosters.

Step 4 turned out to be testable after all: `api.nhle.com` serves a completed
season on request, so the scraper, the maths and the calibration all ran
against the real 2025-26 season. What cannot be tested without a season of
per-game player outcomes is whether the adjustment *improves* accuracy;
`daily_player_stats` is empty, so that stays open.

Step 5 was taken ahead of step 4 because step 4 is the only one that needs data
this repo does not have: the team-strength scraper has to be ported and would
read as zeros until a couple of weeks into a season.

## What the old app does

`get_optimal_lineup(players, lineup_settings)` is a four-pass greedy over
`total_rank`, run once per day per team from three call sites. Around it:

| Old function | Job |
|---|---|
| `get_optimal_lineup` | seat today's players into the league's slots |
| `_get_ranked_roster_for_week` | fetch a team's roster, attach schedule + opponent, compute `total_rank` |
| `_get_daily_simulated_roster` | apply planned add/drops as of a given date |
| `_calculate_unused_spots` | per-day empty slots, for the "you have room" hints |
| `calculate_and_add_category_ranks` | build `total_rank` from bucketed per-category ranks |

The shape is right — daily, position-aware, transaction-aware, run for both
sides of the matchup. What follows is what to change on the way in.

## Decisions

### 1. `total_rank` goes; the draft-prep value engine comes in — built

**Decided: drop `calculate_and_add_category_ranks` entirely. Do not port it.**

It buckets each category into `1,2,3,4,5,6,7,8,9,10,15,20` by percentile. The
last two buckets cover the 50th–75th and 75th–100th percentiles, so for roughly
**half the player pool the objective is a literal constant** and ties break on
DB row order. Three further faults: skaters and goalies are ranked in separate
pools over different category sets and then summed into one globally-sorted
number; the missing-data sentinel is a hardcoded `60` whose meaning depends on
how many categories the league scores; and the flat sum has no notion of which
categories the manager actually needs.

`ranking_utils.py` already does this properly — `calculate_points_ranks` for
points leagues, `calculate_capped_z_scores` for category leagues, both driven by
the league's own settings. That is the engine to use.

Three adaptations it needs:

- **Per-game, not season.** The current engine works on `proj_*` season totals
  against a `projectedGames >= 40` baseline. The daily optimizer needs per-game
  rates, and the z-normalisation has to be against a per-game baseline or the
  scale is wrong.
- **No replacement level.** `rank_mode='projection'` (scarcity weight 0), not
  VORP. VORP is a *draft* concept: it prices the opportunity cost of a roster
  spot. In a daily lineup the alternative to starting player A is starting
  player B who is **already on the roster** — the opportunity cost is B, not
  replacement level. Worse, the matcher (below) enforces positional scarcity
  *exactly*, as a hard constraint, so subtracting a scarcity term from the
  values would count it twice and distort which slot a multi-eligible player
  gets seated in.
- **Goalie multiplier off.** `goalie_multiplier_power=0`. Same argument: it is a
  draft-board correction for goalies filling a niche role, and the G slot count
  is a hard constraint here.

**Carry raw per-category projections, not a pre-summed score.** The optimizer
should hold `{cat: projected_value}` per player per night and collapse it to a
scalar only at the last step, via weights. With weights `1/σ_population` this
reproduces z-sum exactly; §5 swaps in matchup-aware weights. Pre-summing throws
away the information §5 needs.

### 2. Replace the four greedy passes with exact matching — built

Seating players into slots subject to eligibility **is max-weight bipartite
matching**. Rosters are ~20 players and ~12 slots, so an exact solve (Hungarian
or min-cost max-flow) costs microseconds and is *provably* optimal for whatever
value function it is handed.

This deletes: Pass 1 (single-position pre-seed), Pass 2 (the O(n²) scarcity
tiebreak, whose direction is not self-evidently right), Pass 3 (fill-by-shuffle),
Pass 4 (upgrade-by-shuffle), and both nested re-seating blocks. Roughly 120 lines
of heuristic become one call.

It also fixes a live defect: Pass 4 iterates `for benched_player in player_pool:`
while calling `player_pool.append(...)` inside the loop, so displaced starters
are re-examined within the same iteration. Probably bounded in practice, but
unintended and not analysable.

`ranking_utils.eligible_slots()` already implements the Yahoo slot vocabulary
(including F/W/Util generics) and should feed the matcher's edge set.

Do this step **first**, keeping the existing objective, so there is a testable
baseline before the objective changes underneath it.

### 3. Goalies: expected starts, not "his team plays, so he starts" — built

Today a goalie whose NHL team plays is seated at his full per-game projection.
Roster both of a team's goalies in a 2-G league and both "start". This is the
daily-lineup face of the known projection issue in CLAUDE.md — projected goalie
games summing to ~2,974 against a league total of 2,688.

**The governing invariant: exactly one goalie starts each NHL game.** So for any
given game, `Σ P(start)` across that team's goalies = 1. Normalise to it. That
single constraint is what removes the double-count, and it generalises the
back-to-back rule rather than special-casing it — a B2B pair simply distributes
two starts across the tandem instead of giving each goalie two.

Per-night value:

```
value(G, night) = P(start | this game) × per-start projection
```

**Measured while building it.** The league-wide overcount is only 0.3% (2,697
projected starts against 2,688 team games) but per team it is severe: DET is
projected for 113 starts across 84 games. Teams falling *short* mostly do so
because `apply_rookie_projections.py` leaves `proj_gamesStarted` NULL on
imported rookies (4 goalies, on BOS/MTL/PIT/UTA) - a pipeline gap worth
closing on the next preseason run, worked around meanwhile by falling back to
projected appearances. And the two directions need opposite treatment - over the game count
is real competition and everyone scales down, under it means the pipeline has
no projection for whoever takes the rest, and scaling up would hand one goalie
all 84 starts. The shortfall goes to a residual goalie who only absorbs it.

Sources for `P(start)`, cheapest first:

1. **Season start share** (`true_start_pct` in the old repo) as the prior.
2. **Back-to-back split.** On a B2B, shift probability off the presumed starter
   for game 2 and onto the backup. The team-level sum-to-one constraint then
   handles the "count 2 games as 1 start" intuition automatically, and does it
   correctly when the tandem is 60/40 rather than 100/0.
3. **Confirmed-starter feed** on game day, overriding the prior when available.

Two details that will bite:

- **Only counting stats scale.** W, SV, GA, SA, SHO multiply by `P(start)`.
  SV% and GAA are rates and do not — they enter as ratios and need the
  denominator handling in §5.
- **Confirm what the projections are per.** `calculate_goalie_projections.py`
  builds season totals over starts assigned by `goalie_workload.flatten_starts`.
  Multiplying by `P(start)` is only correct if the daily figure is a
  **per-start** rate. If it is already a per-scheduled-game figure, the workload
  assignment has done this once and doing it again double-discounts. Nail this
  down before writing the multiply.

### 4. Opponent strength: per-category, small, mean-neutral — built

The old daily scrape (`jobs/toi_script.py: fetch_team_stats_summary`) collects
`pp_pct, pk_pct, gf_gm, ga_gm, sogf_gm, soga_gm` plus trailing-7-day `_weekly`
variants. **None of this exists in this repo yet** — the scraper has to be ported
alongside. Note also that `pp_pct` is scraped but never reaches
`opponent_stats_this_week`; the key list omits it.

**One blanket multiplier is wrong**, because the opponent stat that drives each
category is different, and for goalies two categories move in *opposite*
directions off the same input:

| Category | Opponent driver | Direction |
|---|---|---|
| G / A / P / GWG | `ga_gm` | weak defence → boost |
| SOG | `soga_gm` | allows shots → boost |
| PPG / PPA / PPP | `pk_pct` | **low** PK% → boost |
| HIT / BLK / PIM | *nothing we collect* | leave flat |
| Goalie W, GA, GAA | opponent `gf_gm` | weak offence → boost |
| Goalie SV (counting) | opponent `sogf_gm` | **high** shot volume → boost |
| Goalie SV% | opponent `sogf_gm` | high volume → slight *penalty* |

A single "weak opponent" boost would push SV and GAA the same way, which is
backwards. And a goals-based multiplier applied to HIT/BLK is noise dressed as
signal — leave those flat and say so in the UI.

**Shape and cap — revised on measurement; the ratio form was wrong.** The real
2025-26 spreads are +24%/−22% for goals against but only +7%/−9% for penalty
kill, so a ratio under a shared cap would leave GA permanently clamped — the cap
doing all the work — while PK barely moved. Use a z-score instead:
`1 + clamp(k·z·direction, ±cap)`, comparable across categories whatever their
spread and exactly mean-neutral where a ratio is not. Measured calibration:
k = 1.5% per σ, cap 4%.

The original sketch, kept for its reasoning: `1 + k·(league_mean/opp_value − 1)`, clamped to **±4%**.
The arithmetic is worth stating, because it is what makes 4% the right
neighbourhood: on a 0.8 pts/gm player, 4% is 0.032 pts/game, ~0.13 over a
4-game week. That will essentially never move a player past someone a tier
above him — which is the requirement, no 4th-liner over McDavid — but it will
routinely break ties between near-equals, which is the entire point. Make the
cap a named constant so it can be tuned in-season once there is enough data to
see whether it is doing anything.

Three properties it must have:

- **Mean-neutral across the league.** If the average multiplier over all games
  is not 1.0, every projected total drifts — and since the whole point is
  comparing your total against your opponent's, an asymmetric drift silently
  biases every matchup. Assert this in a test.
- **Regressed toward the mean by games played**, the same trick
  `calculate_goalie_projections.REGRESSION_GAMES` already uses. This makes the
  adjustment fade in over the first weeks of the season on its own, rather than
  needing a hardcoded "off until October 20th" gate. The `_weekly` table is a
  3–4 game sample and should carry little weight in the optimizer — better as a
  display-only hot/cold flag.
- **Applied to both teams or neither.**

Two effects likely **larger** than opponent GA, and free from the schedule
already in `nhl_schedule`: **home/away** (~2–3%) and **back-to-backs** — which
for goalies change *who starts*, not merely how well they play, and so belong in
§3 rather than here.

### 5. Category-weighted lineups — built

A H2H category league is not won by maximising production. It is won by
maximising **expected categories won**. A unit of production in a category
you're winning by 15 or losing by 15 is worth ~0; a unit in a category within
reach is worth a lot.

```
value(player, day) = Σ_cat  projection[player][cat] × marginal_worth(cat)
```

Model each category's end-of-week margin as roughly normal, with
mean = (my projected remaining − opp projected remaining + current margin) and
standard deviation σ_cat. Then `marginal_worth(cat) = φ(margin/σ) / σ` — the
normal density at the current margin. Points comfortably ahead → low density →
assists cheap. Shots near-tied → high density → SOG expensive. The playmaker
sits, the shooter plays.

Note this subsumes §1: **z-sum is the special case where the weights are the
population inverse-σ.** So §5 is a re-weighting of the same objective, not a
different system.

Four things that will bite:

- **σ per category is not optional.** "Winning by 5" is meaningless without it —
  5 points is a lock, 5 hits is a coin flip. `daily_player_stats` can supply an
  empirical σ; a Poisson approximation (`σ² ≈ mean`) is a serviceable first cut
  for counting stats.
- **Weights depend on the lineup, which depends on the weights.** Marginal worth
  is a function of the projected end-of-week margin, which is a function of
  lineups not yet set. Iterate: flat weights → project margins → recompute
  weights → re-optimise. Two or three passes captures nearly all of it. This is
  why §2 must land first — you cannot iterate a greedy heuristic and trust the
  fixed point.
- **Ratio categories break the linear sum.** SV%, GAA and FOW% move numerator
  and denominator together, so marginal value is not linear in the projection.
  First-order approximation around the projected denominator, or exclude them
  from the weighting in v1 and be explicit about it.
- **Keep the opponent on flat weights.** They will play their best players, not
  counter-optimise against you. Assuming otherwise makes you exploitable.

**Weight on the projected final margin, never the current one.** This is the
rule that resolves the tension between "weight long-shot categories from day 1"
and "don't punt early":

- A category the *projections* say you lose by 3σ is genuinely worth ~0 on
  Monday morning. Downweighting it on day 1 is correct, not premature.
- A category where you're *currently* down 10 hits but the rest-of-week
  projection says it closes is still live. Downweighting that would be the
  premature punt.

Both cases fall out of one rule if the margin fed to `φ` is the projected final
margin rather than the realised one. Keep a **floor** on the weights regardless
— a 3σ projection can be wrong, and the cost of retaining a small weight is
near zero.

### 6. Modelling the opponent's transactions

Currently the opponent's roster is frozen: no adds, no drops. That is a
systematic under-projection of every opponent, and the size of the error depends
on how that particular manager plays — which is exactly why one number is the
wrong output.

**Report a band, not a point.** "Opponent projects 412–447" where the floor is
no-moves and the ceiling is their historical add rate applied. The decision made
from this — which category to chase — is far more robust to a band than to a
false-precision point.

Four tiers, in build order:

**Tier 0 — no moves.** What exists today. Keep it, but label it as a floor.

**Tier 1 — fill the holes.** `_calculate_unused_spots` already computes the
opponent's empty slots per day. Assume each empty slot is filled by the best
available free agent at that position who plays that night (`free_agents` is
already in the schema). Cheap, needs no behavioural model, and corrects the
largest and most systematic bias: nobody leaves six slots empty across a week.

**Tier 2 — the dead-weight swap.** For each opponent roster player, compute
projected starts this week. A player starting in zero or one of the remaining
days — blocked by a position glut or simply not playing — is a drop candidate.
Model swapping him for a free agent at a position with open slots who would
start more. Bound by the league's remaining add limit and waiver rules.

**Tier 3 — manager profile from history — built** (`manager_profiles.py`).
The best version, and the data was already there: `transactions` holds a full
season of every manager's real add/drop history. Two numbers classify a
manager, and both spread widely enough across the 254 imported managers to be
worth splitting on — adds per week runs 0.26 to 3.26 between the 10th and 90th
percentiles, median hold 4 days to 87. Measured thresholds give 107 streamers,
97 targeted and 50 inactive:

- **adds per week** — how active they are.
- **median hold duration** — a manager whose adds are mostly dropped inside 3
  days is a games-played streamer; one whose adds are held for weeks and are
  high-ranked is chasing hot hands and key additions.

Simulate the streamer as Tier 1/2 (fill every hole, maximise starts). Simulate
the hot-hand manager as an upgrade to their best slot, not a games-played
filler — a small number of high-value adds rather than many marginal ones.

**Never simulate more moves than that manager has historically made**, and never
more than the league's transaction limits allow. An opponent model that invents
activity is worse than one that assumes none, because it is wrong in a direction
the user cannot see.

## Other things to fix during the port

- **The same day's lineup is computed three times** in the old matchup endpoint:
  once in the remaining-days projection loop (~2129), once in the display game
  counts loop (~2212), once inside `_calculate_unused_spots` (~2235). Compute
  per day once and reuse.
- **"Non-injured" means only "not in an IR/IR+ slot."** A player who is out
  tonight but not IR-slotted starts at full projection. The preseason
  `scrape_injuries.py` / `apply_injury_adjustments.py` apply a *season-long*
  expected-value haircut, which is the wrong instrument for a daily decision —
  daily needs a hard exclusion from a current injury feed.
- **`game_dates_this_week` vs `game_dates_this_week_full`** — two key names for
  one concept (base roster vs simulated adds), checked with `or` in some paths
  and not others. The matchup loop at ~2126 checks both but skips the IR filter
  entirely, so an IR-eligible simulated add would be started. One key name.
- **The opponent never receives simulated moves** (~2127 uses the raw roster).
  Correct for add/drop simulation, blocking for trade simulation.
- **Pass 2 compares players with `other == player`** — dict equality, expensive
  and wrong when two rows happen to be equal-valued. Compare `player_id`. Moot
  once §2 lands, but it is the kind of thing that gets copied forward.

## Build order

The dependencies are real, not stylistic:

1. **Exact bipartite matching**, existing objective. No intended behaviour
   change beyond strictly-better lineups, so it is testable against the old
   implementation.
2. **Real projected value** from `ranking_utils`, per-game, no replacement
   level, raw per-category values retained.
3. **Goalie start probabilities**, normalised to one start per team game.
4. **Opponent strength**, per-category, ±4%, mean-neutral.
5. **Category weighting**, on projected final margins, iterated to a fixed
   point.
6. **Opponent transaction modelling**, tiered, reported as a band.

Steps 4 and 5 before step 2 would be wasted work: a ±4% opponent adjustment
layered on an objective quantised into 20-point buckets is swallowed whole, and
nobody would be able to tell whether it worked.
