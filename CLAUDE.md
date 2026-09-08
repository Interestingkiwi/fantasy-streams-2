# CLAUDE.md

Guidance for working in the **Fantasy Streams** repo.

## What this is

A Flask web app that gives Head-to-Head fantasy hockey managers advanced analytics,
draft prep, lineup/streaming help, and (planned) automated Yahoo waiver transactions.
Previously deployed on Render.com. Author: Jason Druckenmiller.

## Tech stack

- **Backend:** Python 3.14, Flask 3.1, blueprint routing, SQLAlchemy 2.0 (Core, not ORM)
- **DB:** PostgreSQL — local via `DATABASE_URL` in `.env`; production on Render.
  `render_backup.db` is an 850 MB SQLite snapshot (git-ignored); `export_postgres.py`
  bridges Postgres -> SQLite.
- **Data/math:** pandas, numpy
- **Scraping:** requests, BeautifulSoup4, lxml, cloudscraper
- **Frontend:** Jinja templates + Tailwind (vendored `static/tailwind.js`), vanilla ES6,
  dark VS Code-style theme. No build step.
- **Auth:** Yahoo OAuth2 (authorization-code), hand-rolled on `requests` — see *Auth*
- **Background:** Redis + RQ (`jobs.py`, `worker.py`); falls back to a thread with no Redis
- **Planned but not yet wired:** Gevent, the league-sync ETL, `yahoo_fantasy_api` writes

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Entry point; registers the `main`, `auth`, `draft`, `league`, `schedules` blueprints; `python app.py` -> debug server on :5000 |
| `routes/main_routes.py` | `/` (renders signed-in or signed-out from the session), `/terms`; `/standalone` still a placeholder |
| `routes/auth_routes.py` | Yahoo OAuth: `/login`, `/callback`, `/logout`, `/api/session`, `/api/my_leagues`, `/api/switch_league` |
| `yahoo_auth.py` | Yahoo OAuth2 client — consent URL, code exchange, token refresh, authenticated API calls (see *Auth* below) |
| `config.py` | `Config` from env + `check_config()` fail-fast at startup |
| `schema.py` | Idempotent DDL for the admin + per-league tables; runs at startup (`SKIP_SCHEMA_INIT=1` to bypass) |
| `jobs.py` / `worker.py` | `enqueue()` — RQ when `REDIS_URL` is set, background thread when it isn't |
| `routes/draft_routes.py` | `/draft-prep/` page + JSON APIs: `/api/available-stats`, `/api/projections`, `/api/rank-players` (POST), `/api/playoff-schedule` (POST) |
| `routes/league_routes.py` | `/league/` League Database viewer + read-only APIs, all scoped to the session's league |
| `routes/schedule_routes.py` | `/schedules/` NHL Schedule Insights; reads `nhl_schedule` only, so it needs no league and no Yahoo |
| `schedule_utils.py` | Light nights, per-team game counts, Mon-Sun week derivation — shared by draft-prep and Schedules |
| `db.py` | **Canonical** SQLAlchemy Core engine + query helpers for the web app (`from db import engine, text`) |
| `ranking_utils.py` | Ranking engine — see *Ranking* below |
| `lineup_utils.py` | Daily lineup matcher — seats a night's players into the league's slots, exactly. See *Lineups* below |
| `daily_value.py` | Per-game, per-category player values for the matcher to score against. See *Lineups* below |
| `goalie_starts.py` | How likely each goalie is to start each night, balanced to one start per team game. See *Lineups* below |
| `matchup_weights.py` | Weights each category by how much it is still in doubt, and iterates a week's lineups against them. See *Lineups* below |
| `manager_profiles.py` | Classifies an opponent's add/drop style from their real transaction history. See *Lineups* below |
| `opponent_strength.py` | Per-category nudge for which NHL team a player is facing. See *Lineups* below |
| `scrape_team_stats.py` | Scrapes every team's strength into `team_stats`; feeds `opponent_strength.py` |
| `preseason_db_build/` | Offline pipeline that builds the `final_projections` table (see below) |
| `preseason_db_build/db_config.py` | Pipeline-only SQLAlchemy `engine` from `DATABASE_URL` |
| `preseason_db_build/season_config.py` | `season_game_count()` — season length read from `nhl_schedule` (84 from 2026-27) |
| `templates/index.html` | Landing/login page |
| `templates/pages/draft-prep.html` | ~1,375-line draft prep UI: League Settings modal, projection table, ranking controls |
| `templates/pages/league-database.html` | League Database viewer — settings, teams/rosters, schedule, transactions, player pool |
| `templates/pages/schedules.html` | NHL Schedule Insights — games by team, light nights, per-night calendar |
| `templates/partials/page-nav.html` | Shared nav; `{% set active = '...' %}` before including. Stand-in for the deferred `home.html` shell |
| `static/styles.css` | **Shared design tokens + component classes.** Every page links it; no page declares its own colours |
| `tests/` | Standalone suites, no pytest — `python tests/run_all.py` (see *Tests*) |
| `import_legacy_league_data.py` | Transitional: copies the old deployment's per-league tables in as local fixtures |

## Auth (`yahoo_auth.py` + `routes/auth_routes.py`)

Standard OAuth2 authorization-code flow, hand-rolled on `requests`:

1. `POST /login` — validates the terms tick and the League ID, then returns a
   Yahoo consent URL. A random `state` and the typed league id go into the
   session for the round trip.
2. `GET /callback` — verifies `state` (a mismatch is a forged callback *or* an
   expired session; both land back on `/` with a message, never a 500), swaps
   the code for tokens, stores them against Yahoo's `guid`, and fetches the
   user's NHL leagues.
3. The signed session cookie holds only `guid`, the selected `league_id`, and
   the cached league list — **never a token**. Tokens live in `users`.

`get_valid_access_token()` refreshes 5 minutes before expiry and writes the new
pair back; `api_get()` also retries once on a 401, since Yahoo can revoke a
token before its stated hour is up. Yahoo omits `refresh_token` from some
refresh responses, so the upsert COALESCEs it rather than nulling it.

**Finding the guid** (`resolve_guid()`), cheapest source first: the token
response's `xoauth_yahoo_guid`, then the `sub` claim of its OIDC `id_token`,
then an API call. **Not every Yahoo app returns `xoauth_yahoo_guid`** — ours
does not — and the API fallback needs Fantasy permission the app may not have
yet, so the id_token is the one that works regardless. Its signature is not
verified on purpose: it arrives on a server-to-server TLS call from Yahoo's
token endpoint, not from the browser.

**`scope` is required and is not implied by app registration.** Registration
caps what the app *may* request; the token carries only what the authorization
request actually asks for. `Config.YAHOO_SCOPE` defaults to `fspt-w` (Fantasy
read/write — Phase 4 writes rosters); `fspt-r` is read-only, and adding
`openid` also returns an `id_token`.

Omitting it was a real bug, and an expensive one to read: Yahoo answers an
unscoped token with **403 "This application is not authorized to perform this
action"**, which points at the app registration when the app is in fact fine.
So on a 403 suspect the scope first — `_forbidden_hint()` leads with that.
Changing the scope does not upgrade tokens already issued; the user must sign
in again.

**Parsing Yahoo JSON:** entities come back as lists of partial dicts under
numeric string keys. `_iter_leagues()` walks for the key it wants and merges,
rather than indexing by position, which is what made the old parsers brittle.

**`YAHOO_AUTH_JSON` is deliberately not ported.** The old repo kept one
long-lived token blob (key, secret, access, refresh, guid) in that env var and
wrote it to a temp file for `yahoo_oauth.OAuth2`, so the ETL worker could call
Yahoo with no browser session. It was never part of the login path. Here, each
user's tokens live in `users` and the Phase 2 worker picks whose to use via
`league_updaters` — no shared admin token, and no file handling.

**A 403 on every Fantasy endpoint is a registration problem.** A token response
carrying no `xoauth_yahoo_guid` means Yahoo issued a token with no user
identity, and no client change fixes it. Confirmed by the old repo failing
identically on a fresh login while still serving previously-synced rows from
Postgres — which is what made it *look* healthy.

**Yahoo gated the API behind an application process (Sept 2026).** This is the
actual cause of the 403/401 saga, and it is not a code or registration problem:
Yahoo restricted Fantasy API access and revoked existing grants, so every app —
old and new — now authenticates fine and is refused by every Fantasy endpoint.
Access is requested through Yahoo's application process; an application was
submitted 7 Sept 2026 and **nothing here can work until it is granted**. Do not
debug the auth code against these symptoms.

**Registering the Yahoo app**, as observed on the Create Application form:

- There is **no Fantasy Sports permission**. The only API Permissions offered
  are *OpenID Connect Permissions* and *TW Auction* (Yahoo Taiwan Auctions,
  irrelevant). Ticking OpenID Connect was tried and did not help — it is not a
  substitute for Fantasy access, and an earlier claim here that it "must be
  ticked" was a guess, now retracted.
- Client type is **Confidential Client** (the server holds the secret).
- **Redirect URI(s) accepts several** ("specify any additional redirect uris"),
  so one Yahoo app can serve more than one deployment.
- Observed but unexplained: an app with *no* permissions returned an `id_token`
  while one with OpenID Profile did not. Worth re-checking once access is
  granted rather than reasoning about it now.
- **Scope cannot request Fantasy access — do not try again.** Probed against
  Yahoo's authorize endpoint with a live client_id: only *no scope* and
  `openid` are accepted. `fspt-w`, `fspt-r`, `profile`, `sdct-r` and
  `openid fspt-r` each come back `error=invalid_scope`. `fspt-*` belongs to
  Yahoo's OAuth1-era permission model and is not an OAuth2 scope value.
  `YAHOO_SCOPE` therefore defaults to empty; `openid` is the only useful
  setting, and only to force an `id_token` for `resolve_guid()`.

### When Yahoo API access is granted

The auth code is believed correct but has **never once completed a real Fantasy
call**, so treat the first login as unproven. Work in this order.

**0. Check `YAHOO_SCOPE` is unset on Render.** It was set to `fspt-w` during
debugging, and while set, login fails before Yahoo even shows the consent screen
(`error=invalid_scope`). Empty is correct.

1. **`python tests/run_all.py`** — should pass unchanged. The suites run against
   a stub Yahoo, so they prove the code, not the grant.

2. **Log in and read the log line `Yahoo token response fields:`.** This is the
   single most informative signal:
   - `xoauth_yahoo_guid` present -> the token carries a user identity; the rest
     should follow.
   - absent -> `resolve_guid()` falls back to the `id_token`, then to an API
     call. Absent *and* a 403/401 on every endpoint means the grant still is not
     live; do not start editing the auth code.

3. **Re-run the scope probe** before assuming scope is still a dead end — the
   answer may change with a granted app. Build the authorize URL with a live
   `client_id` and try `<none>`, `openid`, `fspt-r`, `fspt-w`; anything not
   returning `error=invalid_scope` is accepted. Set `YAHOO_SCOPE` on evidence,
   never on a guess.

4. **Only then** debug code. One arbitrary choice to look at first: `_post_token()`
   sends client credentials in the request body rather than as HTTP Basic auth.
   Both are valid OAuth2; this matches the old repo's working caller, but no
   request ever got far enough to prove it mattered.

Once a real login completes, **Phase 2 (the league ETL) is what unblocks**, and
with it every page that reads per-league tables. Carry these into that port:

- **`players` -> `yahoo_players`.** The old `db_builder` / `jobs/fetch_player_ids.py`
  write a table called `players`; here it is `yahoo_players`, kept clear of the
  NHL-keyed `player_directory` and `final_projections`. Apply the rename.
- **Yahoo player ids are TEXT** in `yahoo_players` but INTEGER in every
  per-league table, inherited from the old schema. `league_routes.PLAYER_JOIN`
  holds the cast; reconcile the column types properly here.
- **`league_updaters.league_id` is TEXT** while the per-league tables use
  INTEGER. Same reconciliation.
- **The season mismatch resolves itself.** The imported fixtures are 2025-26
  against a 2026-27 `nhl_schedule`, which is why anything joining league weeks to
  game dates is empty. A real sync fixes it — not a bug to chase.
- **Drop `import_legacy_league_data.py`** once a real sync populates the tables.

**Local dev:** Yahoo rejects plain `http://` redirect URIs, so a real login
needs an HTTPS tunnel registered as the callback and set in
`YAHOO_REDIRECT_URI`. Without one, use `DEV_BACKDOOR_PASS` — entering
`<league_id>-<pass>` in the League ID box signs in as `DEV_ADMIN_GUID` with no
Yahoo call at all. A wrong password falls through to the normal Yahoo flow.

## Ranking (`ranking_utils.py`)

Two value engines, then one scarcity shift on top.

- **Points leagues** -> fantasy points per game.
- **Category leagues** -> asymmetric capped z-scores against a 40+ GP baseline.

**Replacement level.** `starters = num_teams x roster_slots[pos]`, with bench spots
shared out in proportion to starters. The player one past that cutoff, among everyone
*eligible* at the position, sets the baseline. A player's score is
`value - (weight x min(replacement across their eligible positions))` — the minimum,
because a multi-eligible player slots in wherever they help most. This is what makes
the 25th-best centre worth less than the 15th-best winger when the league starts more
centres than wingers.

**Rank modes** (`SCARCITY_WEIGHTS`) set that weight:

| Mode | Weight | Goalie multiplier | Column |
|---|---|---|---|
| `roster` | 1.0 | off | VORP |
| `balanced` | 0.5 | `mult ** 0.5` | Score |
| `projection` | 0.0 | full | none |

The old goalie category multiplier (`skater_cats / goalie_cats`) fades out as scarcity
fades in, via `goalie_multiplier_power`. **Do not run both at full strength** — that
counts goalie scarcity twice and floats backup goalies into the first round. Measured:
with the multiplier on top of replacement level the top 100 was 25% goalies against a
roster share of 17%; with it off, 16%.

Without `num_teams` and `roster_slots` the scarcity weight is forced to 0, so older
callers get exactly the pre-existing behaviour.

## Lineups (`lineup_utils.py`)

`optimal_lineup(players, roster_slots)` seats one night's players into one
league's starting slots so total value is as high as possible. Slot vocabulary
is Yahoo's: C/LW/RW/D/G plus the generics F (any forward), W (wingers only) and
Util (any skater, never a goalie); BN/IR/IR+/NA are filtered out as
non-starting.

**It is exact, not a heuristic**, which is the whole point of replacing the old
app's four-pass greedy. The sets of players that can be seated simultaneously
form a transversal matroid, and greedy returns a maximum-weight basis of a
matroid — so descending value plus an independence test is optimal in both
total value and number of starts. The independence test is the part the old
code lacked: whether a player fits is not "is one of his slots free" but "can
the players already seated be rearranged to make room", an augmenting-path
search. Against the old implementation on 2,000 random rosters it matched 87.5%
of the time and beat it 12.5%, by 3.11 value units on average. 74us per lineup.

Player identity is the index into the caller's list, not a `player_id`, so a
missing or duplicated id cannot corrupt the assignment. `benched()` therefore
matches on object identity and must be handed the same list `optimal_lineup`
was given.

`seat_all=True` (default, and the old behaviour) starts everyone who fits.
`seat_all=False` benches players valued at or below zero — needed once
category weighting can make a start actively harmful.

### Who the player is facing (`opponent_strength.py`)

A projection is an average over average opposition; on a given night the
player faces someone specific. `scrape_team_stats.py` collects each team's
GF/GA/SF/SA per game plus PP% and PK% into `team_stats`, for two windows —
`season` (what the optimizer uses) and `week` (noisy, kept for display).

**Per category, not one blanket multiplier.** Each category has its own driver,
and for goalies two of them respond to *opposite* things — a shot-heavy
opponent means more saves (good) and more goals against (bad). One "weak
opponent" boost would push both the same way, which is backwards rather than
merely imprecise. Hits, blocks, PIM and faceoffs are deliberately left alone;
nothing collected predicts them.

**Standard deviations, not ratios** — a correction to what OPTIMIZER.md
sketched, and the real numbers forced it. Across the completed 2025-26 season
goals-against spreads +24%/−22% about the mean while penalty kill spreads only
+7%/−9%. Under a shared cap a ratio would leave GA permanently clamped — the
cap doing all the work, erasing every distinction between bad defences and
terrible ones — while PK barely moved. A z-score is comparable whatever the
spread, and is exactly mean-neutral where a ratio is not.

**Calibrated against the real season, not guessed.** 1.5% per σ, capped at 4%.
That puts the extreme teams at 2.6–4.3% and a typical ±1σ opponent at 1.5%.
Measured end to end on the current board: best-vs-worst matchup moves a
player's value **4.5%**, against a rank 1→2 gap of 0.6% and a rank 1→20 gap of
31.3%. So it decides between near-equals and cannot reorder tiers — no
fourth-liner over McDavid.

**Mean-neutral, with a test per category.** If the league-wide average
multiplier were not 1.0 every projected total would drift, and since the point
is comparing your total against an opponent's, an asymmetric drift biases every
matchup. Apply it to both sides or neither.

It fades in on its own: z shrinks by games played, so October barely moves and
the adjustment grows with the sample. No hardcoded date gate.

Not done, deliberately: **home ice**, plausibly bigger than any of this and
free from `nhl_schedule`, but sizing it needs home/road splits this module does
not fetch, and guessing would undo the point of measuring the rest.

### What the opponent will do (`manager_profiles.py`)

Freezing an opponent's roster under-projects every one of them, by an amount
that depends on how that manager plays. `transactions` already holds a real
season of add/drops, and two numbers split the styles cleanly:

- **adds per week** — 0.26 at the 10th percentile, 1.60 median, 3.26 at the
  90th, across 254 managers in the 24 imported leagues.
- **median hold duration** — 4 days to 87, population median 11.

Thresholds come from those percentiles, not from taste, and give 107
streamers, 97 targeted and 50 inactive. `simulation_plan()` turns a style into
a hint for the tiers above: a streamer's adds chase games played, so simulate
them filling empty lineup slots; a targeted manager makes fewer, better moves,
so simulate a few upgrades to their weakest starters instead. **Never simulate
more moves than the manager has historically made** — an opponent model that
invents activity is worse than one assuming none, because it is wrong in a
direction the user cannot see.

**Censoring matters and is handled.** 18% of holds never end — the player was
still rostered when the data stops. They are recorded at their lower bound,
which is safe for a *median* specifically, since an unfinished hold is a long
one and sits above the median either way. That breaks down once more than half
a manager's holds are unfinished (42 of the 254), and `medianIsLowerBound` says
so.

Holds are paired chronologically, not grouped: managers re-add the same player
often enough to matter — 1,396 times in the imported data.

### Which categories are worth chasing (`matchup_weights.py`)

A head-to-head category league is won by maximising **expected categories
won**, not production. So a player's nightly value is
`Σ projection[cat] × marginal_worth(cat)`, where marginal worth is how much one
more unit moves the odds of taking that category — the normal density at the
projected margin, `φ(margin/σ) / σ`. It peaks at a tie and decays fast.

**Weight the projected final margin, never the realised one.** That single rule
resolves what looks like a contradiction — wanting to write off a hopeless
category on day 1, while not wanting to punt one prematurely. A category the
projections say you lose by 3σ is genuinely worth ~0 on Monday. A category
where you are currently down ten hits but the rest-of-week projection closes it
is still live. Both follow if the margin fed to φ is `banked + remaining`.

**Banked production moves the margin but adds no variance**, because it has
already happened — so σ comes from the remaining totals alone. Five points
ahead with 115 points still to play is a coin flip; five ahead with seven left
is nearly banked. Getting this backwards is easy, and there is a test for it.

The weights depend on the lineups and the lineups depend on the weights, so
`optimise_week` iterates — flat weights, project, re-weight, re-optimise — with
damping, settling in two or three passes. The **opponent stays on flat weights
and is projected once**: they will play their best team, not counter-optimise,
and assuming otherwise makes you exploitable.

σ is Poisson (`Var ≈ mean`, so `σ_margin ≈ sqrt(mine + theirs)`) because
`daily_player_stats` is empty until a season runs; `dispersion` is the dial for
replacing it with something measured. A `WEIGHT_FLOOR` keeps a written-off
category from reaching exactly zero, since a 3σ projection can be wrong.

Weights are relative — they normalise to a mean absolute value of one, so a
single-category league always returns 1.0 and says nothing.

### Whether a goalie starts (`goalie_starts.py`)

`daily_value` gives goalies **per-start** numbers, so something has to turn
them into per-scheduled-game ones. The invariant doing the work: **exactly one
goalie starts each NHL game**, so probabilities across a team's goalies sum to
one on any night. That subsumes the back-to-back case rather than
special-casing it — a back-to-back distributes two starts across the tandem
instead of giving each goalie two.

A second constraint pulls the other way: a goalie's probabilities should add up
over the season to the starts he was projected for. Both at once is matrix
balancing, done by iterative proportional fitting. **The nightly constraint is
exact and the season one approximate** — the right way round, since a lineup is
set one night at a time.

Two things measured while building it, both worth knowing:

- **Per-team imbalance is far worse than the league total suggests.** Projected
  starts come to 2,697 against 2,688 real team games — 0.3%, apparently
  harmless. Per team it is not: PIT's goalies are projected for 43 starts
  across 84 games, DET's for 113.
- **The two directions are not symmetric.** Over the game count means real
  competition, so everyone scales down. Under it means nobody is projected for
  the rest, and scaling up would hand one goalie all 84 starts. The shortfall
  goes to a residual goalie who exists only to absorb it. Expected starts
  therefore total ~2,584, not 2,688; the gap is third-stringers nobody
  projected.

The back-to-back tilt only fires when there is a clear number one — a level
tandem gets no invented hierarchy, or row order silently becomes a depth chart.

**Known data gap:** `apply_rookie_projections.py` writes imported rookies with
counting stats but no `proj_gamesStarted` — 4 of the 82 goalies, one each on
BOS, MTL, PIT and UTA, and the reason those teams' projected starts fall short
of their games. `goalie_starts` falls back to `projectedGames`, which slightly
overstates starts and is the right direction to be wrong in. Populating the
column in the rookie step would retire the fallback; it needs a preseason
re-run, and per *The projection pipeline* any re-run from step 8 has to carry
through to step 12 or `eligiblePositions` is dropped.

### What a start is worth (`daily_value.py`)

`value_players(rows, categories)` turns `final_projections` season rows into
per-game, per-category values plus the scalar the matcher sorts on. It also
carries `perGame` per player, because §5's matchup weighting needs raw category
units, not a pre-summed score.

**Three things `ranking_utils` does that this must not**, all correct for a
draft board and all wrong for a lineup:

| | Draft board | Lineup |
|---|---|---|
| Centering | z-score vs the average player | origin is an empty slot, so real zero |
| Capping | clip outliers so one category can't run away | linear, or §5's margin maths is in the wrong units |
| Replacement level | prices a roster spot | the matcher enforces scarcity as a hard constraint already |

So a category value is `Σ polarity × per_game / σ` and a points value is
`Σ points_per × per_game` (fantasy points per game) — **the two league modes
differ only in their weights**, which is the seam §5 plugs into.

Per game, not per season: durability is deliberately absent, because for
tonight's lineup a 40-game player is worth what he produces on the nights he
plays. Goalie rates are per *start* (divided by `proj_gamesStarted`, not
`projectedGames`) so §3 can multiply them by a start probability — which is why
a backup with elite per-start rates currently ranks near the top and will stop
doing so once §3 lands.

Ratio categories (GAA, SVpct) are carried but kept out of the scalar; adding a
start moves numerator and denominator together, so summing them is wrong rather
than imprecise. `supported()` splits a league's categories into scored, rate
and missing — GWG is in no projection column and 8 of the 25 imported leagues
score it, so `value_players` logs a warning rather than dropping it silently.

## Draft prep page

Everything except the ranking-method control lives in the **League Settings** modal:
league structure (categories/points + number of teams), roster settings, playoff weeks,
and the skater/goalie category grids. **Rank Via** (Roster Setting / Projection Only /
Balanced) sits on the page itself, in the filter bar, and re-ranks on click.

State is `localStorage`, all keys prefixed `fs_`: `fs_selectedStats`, `fs_statWeights`,
`fs_leagueMode`, `fs_pimPolarity`, `fs_numTeams`, `fs_rosterMode`, `fs_rosterSlots`,
`fs_playoffWeeks`, `fs_rankMode`, `fs_heatmap` (plus `fantasy_streams_tags` for
player tags).

On load the page ranks against those saved settings by *replacing* the
`/api/projections` call with `/api/rank-players`, never adding to it — the ranking maths
costs ~40ms against a ~30ms payload, so a returning user waits no longer. With no
categories selected there is nothing to rank, and the plain call stands.

**Category heatmap** (`fs_heatmap`, toggled in the filter bar) shades every
category cell green to red. It exists for one drafting mistake in particular:
taking four players who are all strong in the same categories and all weak in
the same others. Two deliberate choices — the scale is anchored on the **mean**,
not the midpoint of min..max, because most categories are heavily skewed
(several hundred players near zero in PPP or hits) and a plain min-max scale
paints nearly everyone red; and nulls are excluded rather than read as zero, so
a skater's absent save total does not drag the goalie scale down. Polarity is
respected via `statPolarity()`, or goals against would glow green exactly when
it should not. The scale is computed over the whole pool rather than the
filtered view, so a green cell means the same thing whatever is filtered in
front of it.

**Two schedule columns**, both display-only and neither touching the rankings.
`Light` is always on: how many of the season's games the player's team plays on a
light night, colour-coded against the 32-team average. It reads
`/schedules/api/team-games` rather than carrying a second copy of the maths, so
the draft board and the Schedules page cannot disagree about what counts as
light.

**Playoff weeks** are display-only and never touch the rankings — they drive a `Playoff`
column showing games in the selected weeks, colour-coded against the league average,
with light-night games (dates at or under `LIGHT_NIGHT_MAX_GAMES` — the
comparison is inclusive, so an 8-game night counts) as a suffix. Week numbers
are Yahoo's; the date ranges live in `data-start`/`data-end` on the checkboxes.

## The projection pipeline (`preseason_db_build/`)

`build_database.py` runs the steps in order via `subprocess`. Roughly:

1. `add_tables.py` — schema/logs
1b. `scrape_nhl_schedule.py` — every team's regular season from `api-web.nhle.com`
   -> `nhl_schedule`. Runs early because the projection steps pace against its
   season length. Reads the season from the API rather than hardcoding dates.
2. `historic_data_skaters.py` / `historic_data_goalies.py` — NHL API season stats
3. `append_advanced_skaters.py` / `append_advanced_goalies.py` — MoneyPuck advanced stats
4. `create_player_directory.py`
5. `scrape_ep_rookies.py` (EliteProspects) / `enrich_ahl_stats.py` (HockeyTech feed)
6. `scrape_injuries.py` (ESPN injuries API)
7. `calculate_skater_projections.py` / `calculate_goalie_projections.py` —
   60/30/10 time-decay weighting of the last 3 seasons (per-game), paced to the
   season length from `season_config`, with production & peripheral trend labels;
   40-game 3-yr minimum, 10-game per-season minimum. Goalie rates are regressed
   toward the league mean by sample size (`REGRESSION_GAMES`), which stops a
   40-game backup out-projecting established starters on rate.
8. `apply_injury_adjustments.py` — final downward adjustments -> `final_projections`
9. `apply_rookie_projections.py` — imported rookies the engine can't model
10. `sync_current_rosters.py` — overwrites `teamAbbrevs` in `final_projections` and
   `player_directory` with each player's current NHL team (offseason trades / signings);
   pulls all 32 rosters from `api-web.nhle.com`. Players not on any current roster keep
   their last-season team string.
11. `prune_inactive_players.py` — drops players finished in the NHL
12. `apply_position_eligibility.py` — fills `eligiblePositions` from
   `position_eligibility.csv` (a Yahoo export: `playerName,team,eligiblePositions`).
   Matches on normalised name, then surname + team; anyone the CSV misses falls back
   to their NHL primary widened to Yahoo's vocabulary (`L`->`LW`, `R`->`RW`).

**Re-running part of the pipeline:** `apply_injury_adjustments.py` rebuilds
`final_projections` with `to_sql(if_exists='replace')`, which drops
`eligiblePositions`. Any re-run from step 8 must carry on through step 12.

External data sources: `api.nhle.com`, `api-web.nhle.com`, `moneypuck.com`,
`eliteprospects.com`, `lscluster.hockeytech.com`, `site.api.espn.com`.

### Season length

The NHL moved to **84 games** from 2026-27. Nothing hardcodes that: `season_config.
season_game_count()` reads the max games-per-team out of `nhl_schedule`, falling back
to 82 with a warning if the table is missing. Anything derived from *past* seasons is a
share of an 82-game season (`HISTORICAL_SEASON_GAMES`) and is converted to a rate before
being applied — skater durability, goalie historical GP, and the assigned starts and
anchor in `goalie_workload.flatten_starts`. `historic_data_goalies.py`'s `/82.0` is
correct as written: it describes seasons that really were 82 games.

**Import-path quirk:** pipeline scripts import `from db_config import engine`
(no package prefix), so they must be run with the working directory set to
`preseason_db_build/`. The web app uses the separate root-level `db.py`
(`from db import engine, text`) and runs from the repo root. Both read the same
`DATABASE_URL`; the split is deliberate, see the docstring in `db.py`.

## Running

```bash
# activate the existing venv, then:
python app.py            # dev server, http://127.0.0.1:5000, debug=True

# rebuild projections (long-running, hits external APIs):
cd preseason_db_build && python build_database.py
```

- **Use `127.0.0.1`, never `localhost`.** Werkzeug binds one address family, so
  on Windows `localhost` costs a flat ~2s per request while the client waits for
  `::1` to fail. `DEV_HOST` overrides the bind address.
- Requires `.env` with `DATABASE_URL` (local Postgres). `.env` is git-ignored.
- Tests: `python tests/run_all.py` (see *Tests* below).
- `requirements.txt` is plain UTF-8. (It used to be UTF-16; if an editor shows CJK
  gibberish, that is a stale copy.)

## Tests

```bash
python tests/run_all.py          # all suites; non-zero exit on failure
python tests/test_scope.py       # or one at a time
```

Standalone scripts, not pytest — the repo carries no test-runner dependency
and these need none. Each starts its own stub Yahoo server on a local port
and points the app at it through the `YAHOO_TOKEN_URL` / `YAHOO_API_BASE`
config seams, so the real OAuth flow runs end to end with no Yahoo
credentials. They use the real Postgres from `DATABASE_URL`, writing rows
under a test guid and deleting them again, so a database must be reachable.

| Suite | Covers |
|---|---|
| `test_oauth_flow.py` | the whole journey: CSRF `state`, token storage, refresh-on-expiry, the 401 retry, league switching, logout, dev backdoor |
| `test_guid_resolution.py` | each `resolve_guid()` path, including the shape that broke in production — a token with no guid against a 403 API |
| `test_scope.py` | consent-URL construction and the 403 hint text |
| `test_lineup.py` | the lineup matcher: brute-forces every assignment for 400 random rosters and requires an exact match on value and starts, then seats real projections into all 25 imported league shapes |
| `test_daily_value.py` | the value engine, leaning on the decisions a reader might mistake for bugs — no centering, no capping, no replacement level, goalie rates per start |
| `test_goalie_starts.py` | start probabilities: one start per team night exactly, season totals approximately, and the teams whose projections do not add up |
| `test_matchup_weights.py` | category weighting: that a contested category outweighs a settled one, that inverse categories keep their sign, and that the same margin is less settled the more is still to come |
| `test_manager_profiles.py` | hold pairing against re-adds, trades and unfinished holds, then the claim the classifier rests on — that managers it calls streamers really do hold pickups for less time |
| `test_opponent_strength.py` | mean-neutrality per category, the per-category directions (including the two goalie ones that oppose each other), and that the adjustment breaks ties without reordering tiers |

Adding a suite means adding its filename to `TESTS` in `run_all.py`.

## Conventions

- Every file starts with a docstring: purpose, `Author - Jason Druckenmiller`,
  `Created` / `Updated` dates. Keep this style on new files and bump `Updated` on edits.
- API routes return `{"status": "success"|"error", ...}` JSON and catch exceptions into
  a 500 with `{"status": "error", "message": str(e)}`.
- Postgres columns are camelCase and quoted in raw SQL (`"positionCode"`, `"final_projections"`).
- Blueprints only; add new areas as a blueprint in `routes/` and register it in `app.py`.

## Porting the old app

`docs/MIGRATION.md` is the plan for bringing the pages from the old repo
(`Interestingkiwi/fantasy-streams`) into this one, with the DB-idiom, background-job,
and Yahoo-auth decisions already settled.

`docs/OPTIMIZER.md` is the design for Phase 3 item 4, the daily lineup
optimizer — written before the port because the decisions in it (drop the old
bucketed category ranks for the `ranking_utils` engine, replace the four-pass
greedy with exact bipartite matching, give goalies start probabilities that sum
to one per team game) are much cheaper to make now than to retrofit.

Phases 0 and 1 are done: config/schema/jobs foundations, and Yahoo OAuth end to
end (see *Auth* above), though the OAuth flow has never completed a real Fantasy
call because Yahoo gated the API — see *When Yahoo API access is granted*.

**Phase 2 — the league ETL (`db_builder.py` -> `league_sync/`) — is next and is
blocked on that grant.** Two Phase 3 pages were built ahead of it, because both
work without one:

- **League Database viewer** (`/league/`) — Phase 3 item 1. Reads the per-league
  tables, which `import_legacy_league_data.py` fills with real fixtures from the
  old deployment.
- **NHL Schedule** (`/schedules/`) — Phase 3 item 2. Reads only `nhl_schedule`,
  so it needs neither a league nor Yahoo.

Everything else in Phase 3 needs Phase 2 first. `DEV_BACKDOOR_PASS` is how to
reach signed-in pages meanwhile.

Already pulled forward out of order: the NHL schedule (MIGRATION Phase 3 item 2) is
built as `nhl_schedule` by `scrape_nhl_schedule.py`, because playoff-week comparison
needed it. Note the old repo's version in `jobs/create_projection_db.py` hardcodes
`START_DATE` / `END_DATE` to 2025-26 — don't port it, the new one reads the season
from the API. When porting anything else from that repo, expect raw psycopg2 with `%s`
placeholders; this repo is SQLAlchemy Core with `text()` and `:name` binds.

## Known issues / cleanup backlog

- **Yahoo has gated the Fantasy API** (Sept 2026); an access application was
  submitted 7 Sept 2026. Until it is granted nothing can sync — see *When Yahoo
  API access is granted* for the resumption checklist.
- The per-league tables hold **imported 2025-26 fixtures**, including other
  people's leagues. Local development only: do not commit the data or load it
  into a deployment.
- `/` and `/league/` consume the session; draft-prep and `/schedules/` are
  deliberately league-agnostic.
- `users.tos_accepted_version` is an INTEGER (`Config.TOS_VERSION`), while
  `index.html` keeps its own `CURRENT_TERMS_VERSION` date string in
  localStorage. Two representations of one thing; reconcile when the terms next
  change.
- Token refresh has no lock: two concurrent requests on an expired token both
  refresh, and the later write wins. Harmless now; revisit with the Phase 2 worker.
- The automation / streaming features are stubs.
- ~~`/api/projections` and `/api/rank-players` each take ~2s~~ **Retired 9/7/2026 -
  measured, and it was never the endpoints.** The query is 7ms, `jsonify` 12ms,
  the whole request through Flask's test client ~30ms. The 2s was a flat
  per-request stall on the dev server, identical for a 1 KB response and a
  990 KB one: `localhost` resolves to `::1` first on Windows, Werkzeug binds
  IPv4 only, and the client waits for the v6 connection to fail before
  retrying. Over `127.0.0.1` the same call is 7ms. Do not paginate these -
  there is nothing to fix. `app.py` now binds explicitly and prints the address
  to use.
- Projected goalie games sum to ~2,974 against a league total of 2,688 (32 x 84), because
  every goalie is projected independently and a starter and his backup can't both hit
  their assigned workload. Long-standing, and unchanged in proportion by the 84-game move.
- `position_eligibility.csv` is a manual export and has to be refreshed each preseason;
  the eventual source is the Yahoo API once OAuth lands.
