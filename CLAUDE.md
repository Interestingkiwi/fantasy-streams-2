# CLAUDE.md

Guidance for working in the **Fantasy Streams** repo.

## What this is

A Flask web app that gives Head-to-Head fantasy hockey managers advanced analytics,
draft prep, lineup/streaming help, and (planned) automated Yahoo waiver transactions.
Deployed on Render (fantasystreams.app); `main` deploys on push. Author: Jason Druckenmiller.

## Where things stand (10/1/2026)

- **The 2026-27 season is under way** (opened 9/29). Render's nightly cron is
  live: `player_game_stats`, `team_stats` and (from 10/1) `player_game_lines`
  fill each morning at 08:30 UTC.
- **Yahoo's Fantasy API is still gated**, so League Home (`/standalone/`) is the
  product: leagues are scraped or typed in and live in the browser, optionally
  in a temporary username/password account. Everything Yahoo would have synced
  comes from **Update from Yahoo** — rosters (with player status), the score so
  far, transactions and bench points — read by the server for public leagues
  and by the bookmarklet for private ones.
- **Yahoo's public read-only API** (`pub-api-ro`) serves a *public* league's
  settings, rosters, scoreboards, transactions and day-by-day rosters with
  stats. It is how public leagues are read; see *Season History*.
- **Test league**: `ROSTER_SCRAPE_TEST` (on outside production) reads the
  completed public 2025-26 league 22705 — which is the previous season of the
  user's own league, **5848** (public, "Albany Hockey Hooligans").
- **The player card is built** (10/1/2026) — tap any player on League Home.
  See *The player card*. It needed one new data source, lines from the NHL's
  shift charts (`game_lines.py`), checked against Daily Faceoff.
- **Season to date Stat Sourcing is built and waiting** (10/1/2026). It opens
  by itself on **2026-10-14**, the day after every team's fifth game, read from
  the schedule; until then production offers it greyed out with the date, and
  local development opens it as a preview (`STAT_SOURCING_PREVIEW`). See
  *Stat Sourcing*. *Combined* is still to come.

## Tech stack

- **Backend:** Python 3.14, Flask 3.1, blueprint routing, SQLAlchemy 2.0 (Core, not ORM)
- **DB:** PostgreSQL — local via `DATABASE_URL` in `.env`; production on Render.
  `render_backup.db` is an 850 MB SQLite snapshot (git-ignored); `export_postgres.py`
  bridges Postgres -> SQLite.
- **Data/math:** pandas, numpy; openpyxl for the draft-prep `.xlsx` export
- **Scraping:** requests, BeautifulSoup4, lxml, cloudscraper
- **Frontend:** Jinja templates + Tailwind (vendored `static/tailwind.js`), vanilla ES6,
  dark VS Code-style theme. No build step.
- **Auth:** Yahoo OAuth2 (authorization-code), hand-rolled on `requests` — see *Auth*
- **Background:** Redis + RQ (`jobs.py`, `worker.py`); falls back to a thread with no Redis
- **Planned but not yet wired:** Gevent, the league-sync ETL, `yahoo_fantasy_api` writes

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Entry point; registers the `main`, `auth`, `draft`, `league`, `schedules`, `standalone` blueprints; `python app.py` -> debug server on :5000 |
| `routes/main_routes.py` | `/` (renders signed-in or signed-out from the session), `/terms` |
| `routes/auth_routes.py` | Yahoo OAuth: `/login`, `/callback`, `/logout`, `/api/session`, `/api/my_leagues`, `/api/switch_league` |
| `yahoo_auth.py` | Yahoo OAuth2 client — consent URL, code exchange, token refresh, authenticated API calls (see *Auth* below) |
| `config.py` | `Config` from env + `check_config()` fail-fast at startup |
| `schema.py` | Idempotent DDL for the admin + per-league tables; runs at startup (`SKIP_SCHEMA_INIT=1` to bypass) |
| `jobs.py` / `worker.py` | `enqueue()` — RQ when `REDIS_URL` is set, background thread when it isn't |
| `nightly_update.py` | The nightly NHL scrape: last night's results, then every team-strength window. Gated on the season having started; deployed as a Render cron |
| `routes/draft_routes.py` | `/draft-prep/` page + JSON APIs: `/api/available-stats`, `/api/projections`, `/api/rank-players` (POST), `/api/playoff-schedule` (POST), `/api/export` (POST, returns an `.xlsx`) |
| `routes/league_routes.py` | `/league/` League Database viewer + read-only APIs, all scoped to the session's league |
| `routes/schedule_routes.py` | `/schedules/` NHL Schedule Insights; reads `nhl_schedule` only, so it needs no league and no Yahoo |
| `routes/standalone_routes.py` | `/standalone/` Standalone mode — lineups, matchup, planned moves and free agents from a hand-entered league; no session, no Yahoo. See *Standalone mode* below |
| `routes/account_routes.py` | `/account/` Temporary username/password accounts that keep each user's leagues server-side. See *Accounts (temporary)* |
| `static/account-sync.js` | Mirrors a league's localStorage keys to and from the signed-in account; drives the account modal. See *Accounts (temporary)* |
| `manage_accounts.py` | Dev CLI: `list` / `delete <username>` accounts, `--render` for Render's database |
| `yahoo_rosters.py` | Every team's roster from a Yahoo league's Starting Rosters page: fetch (public leagues), parse, and match names to projections. See *Scraping rosters from Yahoo* |
| `yahoo_matchup.py` | A matchup's score so far from Yahoo's Matchup page: URL (league, week, team), parse. Reuses `yahoo_rosters`' fetch and errors. See *Scraping the score so far* |
| `yahoo_transactions.py` | A league's adds, drops and trades: Yahoo's public read-only API for public leagues, the Transactions page (via the bookmarklet) for private ones, one compact shape. See *Season History: transactions* |
| `yahoo_league_api.py` | A public league's settings, weekly scoreboards and day-by-day rosters with stats, from Yahoo's public API, cached in `yahoo_public_cache`. See *Season History: left on the bench* |
| `bench_points.py` | Pure: what benched players scored, and the single swaps that would have won or tied a category. See *Season History: left on the bench* |
| `bench_lineups.py` | Bench points for a private league: the bookmarklet's daily lineups plus `player_game_stats`, built into the same shapes the public API gives. See *Season History: left on the bench* |
| `week_planner.py` | `Week` (the roster-independent setup), `plan_week` and the `free_agents` search; pure, the route loads the rows. Also manual lineups (`manual_lineup`, `seat_order`) |
| `week_planner.available` | Every free agent as a player line for the Free Agents table (see *The Free Agents tab*) |
| `goalie_planning.py` | Whether one more goalie start is worth the risk: outcomes, minutes and the limits. See *The Goalie Planning tab* |
| `player_form.py` | PP share, L20/L10/L5 trends and home/road splits from `player_game_stats`, for the Lineups roster view. See *The Lineups tab* |
| `season_stats.py` | A player's season so far and his L20/L10/L5 and home/road windows, in Yahoo codes, ratios rebuilt from their parts. See *The player card* |
| `player_card.py` | Pure: one player's card for League Home's player modal — stats windows, game log, line and PP unit or a goalie's starts, the week's opponents. See *The player card* |
| `game_lines.py` | Lines and power-play units from the NHL's shift charts into `player_game_lines`; run by the nightly job, or `--start/--end` to backfill. See *The player card* |
| `stat_sourcing.py` | League Home's Stat Sourcing: projection rows rewritten to each player's season rate, and the date Season to date opens. See *Stat Sourcing* |
| `schedule_utils.py` | Light nights, per-team game counts, Mon-Sun week derivation with breaks folded in — shared by draft-prep and Schedules. See *Fantasy weeks* |
| `static/fantasy-weeks.js` | The weeks every page numbers by: standard weeks plus a league's edits (`fs_fantasyWeeks`), and the edit operations. See *Fantasy weeks* |
| `db.py` | **Canonical** SQLAlchemy Core engine + query helpers for the web app (`from db import engine, text`) |
| `ranking_utils.py` | Ranking engine — see *Ranking* below |
| `lineup_utils.py` | Daily lineup matcher — seats a night's players into the league's slots, exactly. See *Lineups* below |
| `daily_value.py` | Per-game, per-category player values for the matcher to score against. See *Lineups* below |
| `goalie_starts.py` | How likely each goalie is to start each night, balanced to one start per team game. See *Lineups* below |
| `matchup_weights.py` | Weights each category by how much it is still in doubt, and iterates a week's lineups against them. See *Lineups* below |
| `manager_profiles.py` | Classifies an opponent's add/drop style from their real transaction history. See *Lineups* below |
| `opponent_strength.py` | Per-category nudge for which NHL team a player is facing. See *Lineups* below |
| `scrape_team_stats.py` | Scrapes every team's strength and home/road splits into `team_stats`; feeds `opponent_strength.py` |
| `derive_venue_prior.py` | Re-measures home ice's long-run size (`VENUE_PRIOR`) and how fast a season's own split overrides it. Not a job — it prints constants to copy by hand. See *Home ice* |
| `scrape_game_results.py` | Nightly per-game player results into `player_game_stats` from five NHL.com reports (summary, realtime, time on ice incl. power play, faceoffs, goalies); also backfills any historical range (`--pp-only` / `--faceoffs-only` for just those columns) |
| `preseason_db_build/` | Offline pipeline that builds the `final_projections` table (see below) |
| `preseason_db_build/aging.py` | The age curve. Restates a past season as what it would be worth at the age being projected. See *Ageing* |
| `preseason_db_build/derive_aging_curve.py` | Re-measures that curve from the historic tables. Not part of the pipeline — it produces constants, not rows |
| `preseason_db_build/scrape_yahoo_adp.py` | Current-season ADP from Yahoo's public API into `player_adp`. Standalone, not a pipeline step — see *ADP* |
| `preseason_db_build/db_config.py` | Pipeline-only SQLAlchemy `engine` from `DATABASE_URL` |
| `preseason_db_build/season_config.py` | `season_game_count()` — season length read from `nhl_schedule` (84 from 2026-27) |
| `templates/index.html` | Landing/login page |
| `templates/pages/draft-prep.html` | ~2,070-line draft prep UI: League Settings modal, projection table, ranking controls, saved list tabs, xlsx export |
| `templates/pages/league-database.html` | League Database viewer — settings, teams/rosters, schedule, transactions, player pool |
| `templates/pages/schedules.html` | NHL Schedule Insights — games by team, light nights, per-night calendar |
| `templates/pages/standalone.html` | Standalone League Home — settings bar (your team, week, stat sourcing), then League / Matchup / Lineups / Free Agents / Goalie Planning / Season History tabs. Lineups carries the roster view and the hand-editable nightly grid; every player opens the player card modal |
| `templates/partials/page-nav.html` | Shared nav; `{% set active = '...' %}` before including. Stand-in for the deferred `home.html` shell. Carries the account button, and includes `partials/account.html` (the account modal and the boot data) |
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
- **Retire the temporary accounts** (*Accounts (temporary)*). They hold one copy
  of a league per member who saved it, so the same Yahoo league is duplicated
  across accounts by design — do not deduplicate, drop them: `local_accounts`,
  `account_leagues` and `schema.ACCOUNT_DDL`, `routes/account_routes.py`,
  `static/account-sync.js`, `partials/account.html` (and its include and the
  button in `page-nav.html`), `manage_accounts.py`, `tests/test_accounts.py`,
  and the account lines in the privacy policy. Nothing migrates; the sync
  replaces it. Tell users first — anything they entered by hand that Yahoo does
  not carry (planned moves, manual lineups, edited weeks) goes with it.

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
GF/GA/SF/SA per game plus PP% and PK% into `team_stats`, across six windows:
`season`, `season-home`, `season-road`, and trailing `last-1w` / `last-2w` /
`last-4w`.

**What updates and what does not.** Team rates and the z-scores built from them
are derived at run time, so they move with each nightly scrape. Venue
multipliers are a blend: a fixed long-run prior, with this season's split
folded in as games accumulate (see *Home ice* below). The *calibration
constants* — `PER_STANDARD_DEVIATION`, `MAX_ADJUSTMENT`, `RECENT_WEIGHT`,
`VENUE_PRIOR`, `VENUE_PRIOR_GAMES` and `matchup_weights.CATEGORY_DISPERSION` —
are fixed, measured against completed seasons. That is deliberate:
re-deriving them mid-season on a few weeks of play would chase noise, and the
whole point of measuring was to stop guessing. Re-measure them between
seasons, not nightly — `python derive_venue_prior.py` for the venue pair.

**Recent form: real, weak, and mostly already known.** Tested by predicting
each team-week from windows strictly before it, season-to-date correlates 0.35
with next week's shots allowed while the trailing 4/2/1-week windows manage
0.31/0.24/0.21 — all *worse on their own*. What form adds beyond season-to-date
is a partial correlation of 0.03–0.08. So `RECENT_WEIGHT` folds the 4-week
window in at 25% rather than treating form as a separate signal. That is a
re-weighting of two estimates of the same quantity, not a new effect stacked on
top, which is what makes a modest weight safe — a wrong weight costs precision,
not bias.

**Hot goalies do not stay hot.** Worth recording because the raw numbers look
convincing and are not: a goalie coming off a fortnight at .935+ posts .9037 in
his next start against .8902 for an average one. Control for each goalie's own
season save percentage and the gap vanishes — hot −0.0067 relative to his norm,
normal −0.0071, cold +0.0019, all three confidence intervals overlapping. The
apparent effect was entirely skill: good goalies have hot fortnights more
often. **Do not build a "avoid the hot goalie" adjustment**; what predicts is
the goalie's quality, which the projections already carry.

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

**Calibrated against the real season, and then validated out-of-sample.**
2.5% per σ, capped at 4%. Two independent measurements set it: the league
spread makes ±1σ a typical opponent and ±2.5σ the extremes, and a **split-half
test** — team strength built from the first half of 2025-26, production
measured in the second, so nothing in the test window fed the estimate — says
the real effect is 3.5%/σ on points, 4.9% on goals and 8.4% on shots. Shots
being largest is not noise; shot volume is far more predictable than finishing.

The setting sits deliberately below the evidence, because it comes from one
season and a single split, and an adjustment that reorders the board is a worse
failure than one that under-reacts. At 2.5% the cap bites on 2 of 32 teams —
outliers trimmed, not the league flattened — and a typical opponent moves 1.15%.

**Clamping breaks mean-neutrality, so it is corrected.** Once the cap binds on
an asymmetric z distribution the league mean drifts (~6 basis points here).
Small, and it cancels in a matchup since both sides use the same table, but the
guarantee is cheap to keep exact: `multiplier()` subtracts the league-average
clamped shift, restoring a mean of exactly 1.0.

**Mean-neutral, with a test per category.** If the league-wide average
multiplier were not 1.0 every projected total would drift, and since the point
is comparing your total against an opponent's, an asymmetric drift biases every
matchup. Apply it to both sides or neither.

It fades in on its own: z shrinks by games played, so October barely moves and
the adjustment grows with the sample. No hardcoded date gate.

**Home ice is the bigger effect, and is measured too.** On the completed
2025-26 season teams scored 2.2% more at home, took 2.0% more shots and won
**4.4%** more often. Validated against 47,230 real skater-games as a
within-player home/road comparison: observed 1.0406 for points, 1.0419 for
goals, 1.0413 for shots, against modelled 1.045/1.045/1.040 — within 0.4
percentage points on all three.

That same check found **three venue effects that were missing**: hits **+4.6%**
at home (larger than the goals effect), blocks −2.3%, PIM −4.8%. Saying
"nothing we collect predicts hits" was right about *opponent* strength and
flatly wrong about venue. `peripheral_venue()` measures them off
`player_game_stats`, since `team_stats` has no such columns. Some of the hits
effect is scorekeeper bias — home rinks are generous — but the recorded stat is
what a league scores, so modelling it is right whatever its cause.
`VENUE_DRIVERS` maps each category onto the quantity that actually moves it
(wins for W, goals against for GA, shots against for SV — so a goalie at home
allows fewer goals *and* makes fewer saves).

**2025-26 was a weak year for home ice, and one season barely measures it.**
`derive_venue_prior.py` reads every season's home/road split straight from the
NHL stats API. Over the ten full-crowd seasons 2015-16 to 2025-26 (2020-21,
played without fans, left out) home teams scored **4.2%** above average, took
2.2% more shots, won **8.2%** more often and recorded 1.9% more hits, with
blocks 2.0% and PIM 3.3% below. 2025-26's +2.2% on goals was the lowest in
sixteen seasons, so the "hits larger than goals" comparison above holds for
that season only. And across the ten, the home effect on goals, shots, blocks
and PIM varies **no more than one season's sampling noise alone predicts** —
the effect hardly moves between seasons, and a single season barely measures
it. A first week measures nothing: opening night 2026-27 is five games, and
four home wins read raw would credit every home goalie with 60% more wins.

So each multiplier starts at `VENUE_PRIOR` (the ten-season mean) and this
season's split is blended in with weight `games / (games + VENUE_PRIOR_GAMES)`:
0.4% after opening night, 13% after a month, half by season's end. The 1,300 is
one season, rounded, inside the range the data supports: the most drift ten
seasons cannot rule out (90% upper bound) puts the break-even at 760 games for
wins, 1,040 for PIM, 1,350 for goals and 1,900 for shots. The point estimate of
drift is zero for all but wins, which would say never trust the current season;
ten seasons cannot prove that. Hits before 2015-16 are left out too — the home
effect ran +4-5% then and has sat at +1.2% to +2.4% every season since, a
recording change rather than a trend. `player_game_stats` rows become games at
18 skaters a side.

**With no data it is the prior, not nothing.** Before the nightly job first
runs there is no `team_stats` and no `player_game_stats`, and home ice still
applies at its long-run size — the plan's `homeIce` flag, separate from
`adjusted`, which is opponent strength only. Before this, a deployment that had
never scraped planned venue-blind, and one that had scraped a single night
planned on five games.

**Venue and opponent strength cannot double-count, by construction.** The worry
is real — a team allows more goals on the road, so using an opponent's road
numbers *and* boosting your own home player would count the league-wide home
effect twice. It cannot happen, because an opponent's z is standardised *within
its own split*: against the other 31 teams' road records, not the overall mean.
The league-wide part is therefore exactly zero in z terms and survives only in
the venue multiplier. Verified on the real league — averaging the combined
adjustment over all 32 opponents returns the venue multiplier to 1e-9 — and
home/road straddle 1.0, so a balanced season is unbiased.

Combined, best-vs-worst moves a real player's value **6.7%**, against a rank
1→2 gap of 0.6% and a rank 1→10 gap of 20.8%.

The splits come from the NHL stats API directly rather than being derived from
game results — `nhl_schedule` holds fixtures only, with no scores.

### The nightly job (`nightly_update.py`)

`scrape_game_results` for one night, then `scrape_team_stats` for all six
windows — in that order, since the team windows roll up from games that have to
be in already — then `game_lines` for every game in the 14-day look-back that
has no lines. Lines go last so a shift chart not yet posted costs only the
lines, which the next night picks up. Deployed as a **Render cron**
(`render.yaml`, 08:30 UTC = 04:30 EDT / 03:30 EST, after even a late
west-coast game in either offset).

**Not an `enqueue()` job, deliberately.** It runs on a wall clock rather than in
response to a user, needs no cross-process dedup, and an always-on RQ worker to
run one command a day would be the wrong trade. `jobs.py` and `worker.py` stay
for the Phase 2 league sync, which is user-triggered and does need dedup.

**It waits for the season, so the cron can exist now.** Before opening night it
logs why and exits 0 — a job that failed every night until October would train
its own alerts to be ignored by the time they mattered. Opening night is read
from `nhl_schedule` rather than hardcoded, the same way `season_config` reads
the season length. 2026-27 opens **2026-09-29**, so the first run that does any
work is the morning of the 30th. `--force` overrides the gate for backfills.

One trap this exposed: `scrape_team_stats.team_codes()` asked the standings
endpoint for *today*, which returns nothing before a season has been played —
so the first live run of the year was the one most likely to fail. It now falls
back through earlier dates for the name→tricode mapping, which barely changes
between seasons.

### Per-game results (`scrape_game_results.py`)

The port of the old repo's nightly job (`jobs/toi_script.py`) down to what
everything else was derived from: one row per player per game in
`player_game_stats`, carrying `opponentTeamAbbrev` and `homeRoad`. Runs for
last night by default, or over any range to backfill.

**The historical range is the point.** `api.nhle.com` serves completed dates as
readily as last night's, so a finished season can be pulled on demand — which
is what lets the optimizer's assumptions be checked against real outcomes
before a new season has played a game. Hits and blocks need a third endpoint
(`skater/realtime`); `skater/summary` does not carry them, and 21 of the 25
imported leagues score at least one.

**Power-play time needs two more reports**, for PP Util on the Lineups tab:
`skater/timeonice` for each skater's `ppTimeOnIce`, and `team/powerplaytime`
for his team's (`teamPpTimeOnIce`). The team report has no playerId, so it is
matched onto each player row by `(gameId, opponentTeamAbbrev)`, and paged on
`(teamId, gameId)`, its own unique key. The 2025-26 season predates these
columns and was filled with `--pp-only`, which fetches just those two reports
and writes every `BACKFILL_DAYS` (14). The first attempt fetched the whole
season before writing and lost twenty minutes to a single API timeout, so
requests now retry up to three times on a timeout or dropped connection. That
also helps the nightly run.

**Faceoffs are a fifth report, `skater/faceoffwins`** — `faceoffWins`,
`faceoffLosses`, `totalFaceoffs` per game, for Yahoo's FW and FL. The old repo
collected them; the port had left the report out (the summary carries only a
percentage) until bench points needed them on 9/30/2026. `--faceoffs-only`
fills just those columns over a range already scraped, the same way as
`--pp-only`. Checked against Yahoo's own FW/FL for 23 centres on one night:
identical.

**The player card's columns came free** (10/1/2026): even-strength, shorthanded
and overtime ice time and shifts (`skater/timeonice`), missed shots and
attempts blocked (`skater/realtime`), and even-strength goals and points (the
summary) — all reports already fetched, so no extra request.

**The nightly job fills a gap in any column itself**: `fill_missing` looks
back 14 days (`LOOKBACK_DAYS`) for skater rows missing a column every skater
gets (`FILLED_FOR_EVERY_SKATER`) and re-scrapes those nights. That is how
Render's opening nights got faceoffs (on 10/1, confirmed: every skater row on
9/29 and 9/30, wins equal to losses each night) and will get the card's
columns on the first run after deploy, with no manual write. The look-back is
short on purpose — a player some report never listed would otherwise make
every night re-fetch the season. A team's PP time is not on the list: a game
without a power play is None by right. **Locally, 2025-26 lacks the 10/1
columns** — the card reads only the latest season, so it does not matter; a
full re-scrape of that range would fill them.

**Two ways this endpoint corrupts data silently, both hit during the port.**

*It stops paging at an offset of 10,000 and says nothing.* A season-long
request came back looking healthy — 9,600 rows spanning the right dates — but
the result is sorted before it is truncated, so it was a top-heavy subset
masquerading as a complete season. The range is now split into weekly chunks,
and `PAGE_CEILING` raises if any single query ever reaches the limit.

*Paging is unstable unless the sort is **total**.* Ties are ordered however
the server pleases between requests, so pages overlap and miss. With no sort,
one real night returned 576 rows of which 15 were duplicates — 15 rows lost
outright, and 15% of the hits data with them. Sorting on `playerId` alone
(which is what the old repo's URLs did) fixes a single-day query but still
loses ~5 rows a week, because a window spans several days and a player's own
rows tie with each other. The sort is now `(playerId, gameId)` — the table's
own primary key, unique across any window.

Both are the worst possible input to a calibration, because both look like
success. Each has a regression test.

*The preseason scrapers had the same paging bug.* `historic_data_skaters.py` and
`historic_data_goalies.py` paged `stats/rest/en/{skater,goalie}/*` with no sort
at all, so two identical runs returned 940 rows containing 931 and 928 distinct
players — duplicates written to the table and, for each one, a player missing
outright. They now sort on `playerId`, which is unique per season and so leaves
no ties to break, and each season warns if the distinct count does not match the
`total` the API reports. Runs are reproducible: 4,739 skater-seasons, twice.

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

**The floor is per category — a share of that category's own worth at a tie —
never a share of the largest weight.** Weights are per unit of each stat, and
one shutout is a far bigger unit than one shot, so SHO's weight runs ~200x
SOG's. The floor read "5% of the largest" until 9/16/2026; on a real
ten-category matchup that lifted A, PPP, SOG, HIT, BLK and SV to one identical
weight, so hits at 9% to win counted exactly what shots at 97% did, in any
league scoring SHO or W. It went unnoticed because the unit tests only mixed P
and SOG, which are near the same scale; `test_matchup_weights.py` now carries
the real matchup. Same lesson for anything *shown*: a per-unit weight means
nothing to a reader, so the page shows `contested` (`φ(z)/φ(0)`, 0–1) instead.

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

## Standalone mode (`/standalone/`)

Lineups for anyone who has not linked Yahoo — which, while the Fantasy API is
gated, is everyone. The league and roster are typed in and live in
`localStorage`; each request posts them, so the routes read no session and no
per-league table. Nav label: **League Home**. Signed in to an account, that
localStorage is kept in step with the server — see *Accounts (temporary)* —
and these routes still read nothing but what is posted.

**Laid out like the old site's League Home**, which is the design to follow
(`Interestingkiwi/fantasy-streams`, `templates/home.html`). A settings bar up
top holds what every sub-page reads — **Your Team**, **Fantasy Week** (with
*Only nights still to play*) and **Stat Sourcing**. Below it is a row of
sub-page tabs, **League · Matchup · Lineups · Free Agents · Goalie Planning · Season History**,
and the chosen one fills the panel under it. The site-wide nav keeps the corner
the old Logout button had. Season History so far holds transactions and bench
points; the old site's category strength, and its Trade Helper and Tools tabs,
are still to come.

- Tabs only toggle visibility. Every panel stays in the page and is kept
  current whichever one is showing, so switching tabs never re-plans. The last
  tab is kept in `fs_standaloneTab`, and `#matchup` etc. open one directly.
- **The opponent is picked on the Matchup tab**, as it was on the old site.
  Both dropdowns write `league.mine` / `league.opponent`; the team editor no
  longer has role buttons.
- **Stat Sourcing: Projected, or Season to date once it opens** (2026-10-14
  for 2026-27) — see *Stat Sourcing* below. *Combined* is listed but disabled,
  and the old site's *Show Raw Data* toggle waits on the rank display it
  switched.
- Planned moves live on the Free Agents tab, and the Matchup tab notes when its
  projections count them, like the old Simulated Moves Log.

**First slice: a week of best lineups for one roster.** `week_planner.plan_week`
runs the *Lineups* chain one night at a time — `daily_value` against the whole
pool (σ comes from everyone, not the roster), `opponent_strength.adjust` for the
opponent and venue, re-`score` under the same weights, `goalie_starts` for the
start odds, then `optimal_lineup`. ~20ms for a week; the one slow read,
`peripheral_venue` over `player_game_stats` (~200ms), is cached in-process for an
hour since it only moves nightly.

**An opponent roster turns on the matchup.** Optional — without one the week
is set on flat weights, which is the honest answer with nothing to be in doubt
against. With one, a categories league goes through `optimise_week`: your
lineups chase the categories whose projected final margin is close, the
opponent plays his best team on flat weights, and `week_planner.matchup()`
reports win odds per category, `contested`, and expected categories won. A
points league is never re-weighted (every point is worth a point) but gets both
sides' projected points and a win probability, with σ² = Σ points² × dispersion
× volume across its categories.

Flat weights are normalised to a mean absolute value of one before they reach
`optimise_week`, because it blends them with matchup weights normalised that
way; raw z weights run several times larger and would swamp the blend.

**Score so far** (`banked`, keyed by week in `fs_standaloneBanked`) moves the
final margin but adds no variance — σ comes from the remaining nights alone.
It only makes sense with *Only nights still to play* ticked, or tonight is
counted twice; the page says so. That checkbox appears only for a week already
under way, so it could not be exercised in a browser before opening night
(2026-09-29) — worth a look the first week of the season.

Rate categories (GAA, SVpct) are listed in the matchup but get no odds: their
final value depends on volume that is not projected as a ratio.

`optimise_week` re-values players into new dicts, so benches are found by
`playerId`, not `lineup_utils.benched` — which matches object identity and
would report nobody seated.

### The Lineups tab

Built to the old site's Lineups roster view. From the top:

- **An Opponent picker** that is the same setting as the Matchup tab's (both
  write `league.opponent`), and a **Yours / Opponent's** toggle that switches
  the roster view and the grid together.
- **Summary cards** with the opponent's figure under yours.
- **The roster view**, skaters and goalies in separate tables. It shows each
  player's nights this week (yellow when he starts, grey when he sits, struck
  through when he is out), who he plays, games and expected starts, and his
  games next week. Then PP%, trend arrows for his last 20, 10 and 5 games plus
  H/A for his next game, his draft-board rank, and his unadjusted per-game line
  shaded against the pool. A goalie's name carries his share of starts, and a
  skater's the old site's `L1 · PP1` badge (`game_lines.latest`: his line last
  game, and his unit the last game his team had a power play).
- **Every name opens his card**, and so do the trend, PP% and opponents cells
  and the line badge, each on its own tab — see *The player card*.
- **The nightly grid**, one table, which can now be **edited by hand**.

The server supplies all of it. `plan_week` gives every player his `nights`
(start / bench / out), `nextWeek` (from `next_start` / `next_end`), `perGame`
and `heat`. `heat` is where his line ranks among regulars of his kind: at least
20 projected games or starts, since a call-up's 3-game rate would set the
scale, and flipped for GA and GAA. The route adds `seasonRank`, the same draft-board
ranking the free-agent drops use (so it takes `num_teams` / `draft_slots` /
`roster_mode`, which every plan request now sends), and `form` from
`player_form`.

**Form is descriptive, never fed into a projection.** See *Hot goalies do not
stay hot*. It is read from the latest season in `player_game_stats`, so before
opening night it shows 2025-26, and the page names the season.

- *Trends* compare the last N games with the player's own season mean in
  standard errors of an N-game mean, arrowed past 1.5. Measured on 1,038
  players from 2025-26, that marks about 1 in 10 in each window. At 1.0 it was
  1 in 4, which is not "major outliers". A percentage threshold would flag every
  depth player's good week and no star's slump. A value is the game's line
  under the league's own weights; a goalie counts starts only.
- *PP%* is his PP seconds over his team's across his last 5 games, and in his
  last game. **It never reaches back past those games**: an early version took
  the last five games *that had the data*, so with the backfill half done it
  presented October's usage as recent. `test_player_form` pins this.
- *H/A* needs 5 games at each venue before it claims a preference.

**Manual lineups** (`fs_lineupEdits`, `{date: {sig, seats}}`). **Edit
lineups** turns every seat of your grid into a picker of the players with a
game that night who can fill that slot. The server says who, in each night's
`playing`, so the page never re-implements eligibility. Picking a player who is
already seated swaps the two, or empties his old seat if the displaced player
cannot fill it. A changed night is sent as `lineups` and **held fixed**:
`manual_lineup` builds it, and `optimise_week(fixed=...)` projects it on every
pass but never re-seats it. The matchup, totals, summary cards and best adds
all come from that one plan. Best adds holds manual nights fixed too
(`free_agents(lineups=...)`), so an add nobody seats on a manual night is
worth nothing there.

- *A seat that can no longer be kept is emptied and reported, never
  refilled.* That covers a dropped or out player, a night with no game, a slot
  he cannot play, or the same player twice. The night shows why. A quiet
  substitution would be the optimiser overruling the user.
- *`sig` is the starting slots the night was set under.* A seat is an index
  into `seat_order`, so after a slot change the same list means something
  else. Such nights are ignored rather than misread.
- Each night has a *Reset*, and *Reset all to best* clears the week. Only your
  own side is editable.
- *A manual night is shown seat for seat as it was set.* Lineups are
  `{slot: [players]}`, which has no empty places, so the first version showed
  the second of two C seats sliding into an emptied first one. `manual_lineup`
  therefore also returns `placed`, one entry per seat, and a manual night's
  grid is built from that.

**Goalie odds are balanced over the whole NHL team, not the roster.** Owning
only the backup does not make him the starter, so `_goalie_probabilities` runs
the balancing over every projected goalie on each rostered goalie's team, across
the full season schedule. There is a test for exactly this.

**Storage, and what is shared with draft prep.** Categories, points values and
PIM polarity are draft prep's own keys (`fs_selectedStats`, `fs_statWeights`,
`fs_leagueMode`, `fs_pimPolarity`), so the two pages describe one league.
Starting slots are **not** shared: draft prep's roster settings have no `Util`
or `W` and would drop them on its next save, so the lineup keeps
`fs_lineupSlots`, seeded once from `fs_rosterSlots`. Also `fs_leagueTeams`
(below), `fs_standaloneMoves`, `fs_standaloneWeek`, `fs_standaloneRemaining`,
`fs_standaloneBanked`, `fs_standaloneTab`, `fs_lineupEdits`,
`fs_standaloneGoalieStats`, `fs_leagueTransactions` and `fs_fantasyWeeks` — all
synced to an account — plus `fs_benchLineups`, a private league's daily
lineups, kept on the device only (see *Season History: left on the bench*), and
`fs_statSource`, the Stat Sourcing, a per-device way of looking. The older `fs_standaloneRoster` /
`fs_standaloneOpponent` are read once to seed the league and left in place. Toggling a chip changes only that
column, so a stat draft prep selects that the lineup engine cannot score
survives a visit here.

Draft prep stores categories as projection column names; the engine speaks Yahoo
codes. `week_planner.COLUMN_TO_CATEGORY` inverts `daily_value`'s own tables, so
the two cannot drift. GWG and the derived PPA/SHA have no column, so they cannot
be chosen here yet.

**Out** keeps a player's games counted but never seats him — for injuries until
there is a feed for them. Weeks come from `fantasy-weeks.js` — see *Fantasy
weeks* below — and the League panel carries the editor for them.

### Fantasy weeks (`derive_weeks` + `static/fantasy-weeks.js`)

**Standard weeks follow Yahoo, breaks included.** Mon–Sun, except that a week
holding a league-wide break of `BREAK_MIN_IDLE_DAYS` (4) idle days is folded
into a neighbour and everything after renumbered. In 2026-27 the All-Star break
leaves Feb 4–7 idle at the back of the Feb 1–7 week, so **Week 19 runs Feb 1–14**
and the season has 27 weeks, the last Apr 5–10. Christmas (three idle days) is
left alone. Read from the schedule, not pinned to a date, so next season's break
is found the same way — `test_schedules.py` pins both this season's result and
the rule's shapes (break through Sunday folds forward, from Monday folds back,
across a boundary joins the two).

This fixed a disagreement that had gone unnoticed: draft prep's hardcoded playoff
weeks already assumed the merge (Week 23 = Mar 8–14) while `/schedules/api/weeks`
did not, so every week from February on was numbered one apart between the two.

**Leagues that differ edit their weeks** on League Home's League tab: move a
week's last night (the next week starts the day after; weeks swallowed whole are
absorbed), Merge with next, Split a week over seven days at its first Sunday, or
Reset. Edits live in `fs_fantasyWeeks` as `{season, weeks}`; a different season
start ignores them rather than misapplying last year's, and they never override
a synced league's own weeks. Every operation is pure and returns a contiguous
season from opening night to the last, so there is no gap or overlap to handle.
An edit that lands back on the standard weeks clears the key.

**One module, three pages.** Lineups, draft prep's playoff weeks and the Schedules
page all load weeks through `FantasyWeeks.load()`, so an edit renumbers all three.
Draft prep now builds its playoff checkboxes from the season's last five weeks
(the static markup stays as a fallback if the load fails) and drops a stored pick
that no longer names one of them, rather than leaving it counting invisibly in
the summary. Its checkbox listener is delegated for that reason — the rebuilt
checkboxes would otherwise have none.

`MAX_PLAN_DAYS` is 35: the combined week is 14 days, and a league can merge more.

### League rosters, planned moves and free agents

**Every team's roster is entered**, because a free agent is anyone on none of
them. `fs_leagueTeams` is `{teams: [{id, name, yahooTeamId?, players: [{id, out, status?}]}],
mine, opponent}` — mine and opponent are team ids, `yahooTeamId` is Yahoo's team
number (from the roster scrape, or learned from a matchup scrape by name), and the opponent is simply whichever
team is marked for this week. **That shape is the target for the planned
sign-in roster scan**: a scan the user starts while signed in to their league,
reading each team's roster page, should write exactly this and nothing else
— and *Scraping rosters from Yahoo* below is that scan.
The first visit lays out `fs_numTeams` teams (default 12), with the old two
rosters as the first two, and saves at once so team ids are stable.

**A move is `{add, drop, date}`**, saved per week in `fs_standaloneMoves`. The
added player counts from `date`, the dropped one plays up to the night before —
how an add made before tonight's games lock behaves. `week_planner.roster_on`
applies moves in date order; moves feed the plan like any roster, so lineups,
the matchup and the grid all show them. They are plans, not roster edits: the
team's roster is not changed, and nothing is sent to Yahoo.

### The Free Agents tab

**The table comes first, the recommendation second.** The tab lists every free
agent in the league (`week_planner.available`, `/api/free-agents/pool`) as the
same player line the Lineups roster view shows — one `rosterTable` renders
both, so an add can be read against the player he would replace. It was
recommendations only, which decided for the user; the ranked list now sits
behind **See recommendations** in a modal.

Each row carries his nights this week and whom against, games, next week's
nights, PP%, the trend arrows and H/A, his draft-board rank, and his per-game
line shaded against the pool. Goalies are a separate table, with the share of
his team's starts on the name: a per-start line beside a skater's per-game one
otherwise reads as far better than it is.

**Filters** follow the old site: name, position, *plays every night selected*
(every ticked night, not any — the point is filling a hole), only players with
a game this week, and hide injured. Any header sorts. **Only 150 rows of each
table are drawn**; filters and sorting are how you reach the rest, and the
summary says so.

**Injuries come from `current_injuries`** (the preseason ESPN scrape), as a
badge and a filter, never applied to a projection — `apply_injury_adjustments`
has already done that. The page prints how old the feed is, because a stale
feed fails silently and convincingly (see *The projection pipeline*).

**Open roster spots** is the old site's unused-roster-spots table: seats your
roster cannot fill on each night, from the plan already on the page. That is
the games an add would actually add.

**Add** on a row opens the plan-a-move modal: the drop (roster players, each
with his season rank and any suggested drops marked) and the night, with
**Score this move** running the same exact `evaluate` the recommendations use
and filling every night's gain into the list. Planning it appends to
`fs_standaloneMoves` like any other move.

**The pool is loaded when the tab is first opened**, not on every plan — it is
a second request (~1.3s, ~1MB for 664 players, most of it form and per-game
lines). Anything that re-plans marks it stale and the summary says to Refresh.

**`faMetric` is kept apart from `faResults`.** Scoring one pair from the Add
modal returns a one-candidate response; assigning that to `faResults` made the
recommendations modal show that single pair as the whole list. Only a real
search may write `faResults`; the metric a gain is printed in has its own
variable.

**Best adds** (`free_agents`, `/api/free-agents`) ranks free agents by what
they add to *this week's* metric — expected categories won against an opponent,
projected points in a points league, flat lineup value with no opponent — each
with a drop and a date. Re-planning every free agent x drop x date would be
~670 x 16 x 7 full plans, so it narrows in three passes: screen everyone on
unadjusted weighted value; shortlist 40 against the 3 weakest drops night by
night under the current plan's weights (suffix sums give every date at once);
then re-plan the best 10 exactly on every date. ~0.8s for a 12-team league.
Because the gains are exact on every date, **changing the night in the table
needs no request**; changing the drop re-scores that pair (`evaluate`). A test
pins that the gain shown equals re-planning the week with the move made.

**The recommended date is a recommendation.** It is the date with the largest
gain (earliest on a tie); the user can pick any night, and the table keeps their
pick when the drop changes. The planned move carries whatever they chose.

**Suggested drops come from the draft board, not the lineup engine.** A one-week
gain says nothing about March, so drops are the roster's lowest on
`value_over_replacement` from `calculate_player_ranks` — the same call the draft
board makes, with the league's `fs_rosterSlots` / `fs_rosterMode` and team count
— and never a player marked Out (often stashed on IR, not cut). This had to be
the draft board's number: the first version ranked drops on per-game lineup
value, which is not comparable between goalies and skaters, and suggested
cutting Ilya Sorokin, 13th on the board. Each side of a suggestion carries its
season rank so the long-term cost is visible.

A search goes **stale** as soon as rosters, moves or settings change; the table
says so rather than silently re-running. A stale row whose drop a later move
already dropped shows that drop as "no longer on your roster" and blocks Plan
move — before this, the menu fell back to "No drop" while Plan move would still
have dropped him.

Not modelled yet: waiver periods, weekly add limits, roster size (a move with
"No drop" is allowed and assumes an open spot), and anything past this week.

### The Goalie Planning tab (`goalie_planning.py`)

The question it answers: with the goalie minimum already met, **another start
can only add wins and saves, but it can lose GAA and save percentage.** How
bad a game would that take, and how likely is one?

- **Where both sides stand**: W, GA, GAA, SA, SV, SV% and SHO, so far and
  projected, mine against theirs.
- **Starts still to come**, from the lineups already set, each with the shots
  that goalie is projected to face that night.
- **One more start**: pick a goalie and a night, and every outcome from a
  shutout to a pull, with what each does to your GAA and save percentage and
  whether you still win them - plus the headline, the most goals he can
  concede and keep each category, and how likely that is.

**The whole thing runs off the projections**, not the generic numbers the old
site used: shots come from his `proj_shotsAgainst` adjusted by
`opponent_strength` for that opponent and venue, and goals against are
binomial in those shots at his projected save percentage. A start against a
shot-heavy opponent really is a riskier start, and this says so rather than
offering one fixed "bad game".

**Minutes are the part to be careful with, and none of it assumes 60.**

- *So far*: Yahoo shows GA and GAA but not TOI, and `GAA = GA x 60 / TOI`
  inverts exactly, so `minutes_played` recovers the real minutes - pulls,
  empty nets and overtime included, because Yahoo computed its GAA from them.
  With nothing conceded there is nothing to divide and it counts starts
  instead, at the average below.
- *Projected and per outcome*: measured over 2025-26's 2,624 starts in
  `player_game_stats`, a start averages **58.6 minutes**, not 60 - 24% run
  past 60 into overtime, 5.6% end before 55, and the shortest was two and a
  half minutes. Minutes also move with the night: 59.8 on a one or two goal
  game, 56.6 on a five, 57.6 on a shutout (nothing sends it to overtime, and
  an injury-shortened start concedes nothing). `MINUTES_BY_GOALS` carries that
  curve and every outcome divides by its own.
- *A pull* is the average of the 147 starts under 55 minutes: 30.3 minutes,
  4 goals, 15.7 shots. It is shown **beside** the distribution carrying the
  measured 5.6% share rather than inside it, since a pulled night is one of
  the bad rows and not an extra one.
- **A pull is worse on both ratios, not just GAA** - same goals, fewer saves
  underneath them - but the GAA damage is several times larger in relative
  terms, because the minutes halve while the shots fall by a third. An earlier
  claim here that save percentage "barely notices" was wrong;
  `test_goalie_planning.py` pins the real relation.

**`worst_start` walks the goals one by one** rather than solving in closed
form, precisely because the minutes depend on the goals: a closed form would
have to assume a fixed hour.

The counting stats come from the Matchup tab's Yahoo scrape, which parses GA,
SA and SV even though Yahoo marks them unscored, into `fs_standaloneGoalieStats`
- the page needs them in a league that scores neither. They can also be typed
in. `/api/goalies` builds a **second `Week` over the goalie counting stats**,
because the league's own categories may not include GA or SA and a plan that
never projected them cannot be read for them.

### Scraping rosters from Yahoo (`yahoo_rosters.py`)

**League ID + Scrape rosters** fills every team from Yahoo's
`/hockey/<league id>/startingrosters` page: one page, every team, every slot.
The ID comes from Yahoo's League > Settings (a pasted league URL works too), and
is remembered in `fs_yahooLeagueId`.

**Public leagues are fetched by the server; private ones cannot be.** Verified
with an empty cookie jar: a public league's page comes back complete, rendered
for a signed-out visitor (its only login link is "Sign in"). A private league
(checked against a real one) redirects the same request to `login.yahoo.com`. No
server-side change gets past that, and **the user signing in to Yahoo in their
browser does not help** — the server's request never carries the browser's
cookies, the page cannot read another site's page, and Yahoo refuses framing
(`X-Frame-Options: SAMEORIGIN`).

**So private leagues use a bookmarklet.** The panel offers a "Send rosters to
Fantasy Streams" button to drag to the bookmarks bar, and a link that opens the
Yahoo page with `window.open` (no `noopener`, so the Yahoo tab can post back).
On Yahoo, the bookmarklet posts `document.documentElement.outerHTML` to its
opener and waits for an `fs-received` acknowledgement — a message to a window
that has navigated away is dropped silently, so without the ack a failure would
look like success. With no opener it opens `/standalone/?rosters=receive`,
which announces `fs-ready`, and sends then. The page accepts `fs-yahoo-page`
(and the older `fs-rosters`) only from a `*.yahoo.com` origin, and the HTML only
goes to `/api/rosters/parse` — or `/api/matchup/parse` when the URL is a Matchup
page — to be parsed — never run, never stored. `APP_ORIGIN` is `location.origin`, not
built on the server: behind Render's TLS proxy Flask sees `http`, and a message
aimed at the wrong scheme is dropped. The bookmarklet's source is the
`sendToFantasyStreams` function in the page, serialised — it must name
nothing outside itself.

**One parser for both.** The live page the bookmarklet sends is ~2.2 MB against
the ~1.2 MB served, because Yahoo's scripts rewrite it — checked by running the
parser's selectors on the live DOM: the same 12 teams, 197 players and 5 IR
players as the served page. Teams are `table[id^="Tst-team-"]`, each after a link
to `/hockey/<league>/<team number>` holding its name; each row is `td.pos` (the
slot) and `.ysf-player-name a.name` (title = name, href = Yahoo player id).

**IR, IR+ and NA players stay on their team, marked Out** — still rostered, so
still not free agents, but not playing. Out follows Yahoo on every import.

**Out reads Yahoo's player status as well as the slot.** The slot alone missed
players a manager had not moved: a suspended Charlie McAvoy sat in a D slot
tagged NA ("Not Active", note "Suspension") and was planned as a starter, and
had to be unticked by hand after every re-scrape. The badge beside each name
(`.ysf-player-status`, code as text, meaning as `title`) is now read, and
`OUT_STATUSES` (O, IR, IR-LT, IR-NR, NA, SUSP) marks him Out whatever his slot.
**DTD is a doubt, not an absence** — recorded and shown, still active. The
status is kept on the roster entry (`{id, out, status}`) and shown as a badge on
the League tab and the Lineups roster view. On league 5848 the page gave the
same 15 statuses, 14 of them out, as Yahoo's API. A manual Out tick is still
replaced by the next scrape — only Yahoo's own flags now survive it.

**Matching.** The page has no NHL team or position per player, only the name
and Yahoo's player id. A name matching exactly one projected player is taken.
Otherwise — two projected players with one name, or none — the Yahoo ids go to
the public read-only player API (`pub-api-ro`, as the ADP scrape uses), whose
team and position settle it: team, then position, then alias, then surname on
the same team. On the real test league 196 of 197 matched with two lookups; the
miss was a goalie with no projection, and unmatched players are listed in the
preview, not dropped silently. `normalise`, `TEAM_FIXES` and
`POSITION_WIDENING` are copies of the pipeline's (whose modules import its
database module and cannot be imported by the web app); a test fails if they
drift.

**Nothing is replaced until the user says so.** Every import shows a preview —
teams and players found, how many Out, who could not be matched, a warning when
it replaces entered teams — and asks which team is theirs (required) and this
week's opponent. A team whose name comes back unchanged keeps its id.

**Errors each say what happened**: `invalid_id` (not a number, checked before
any fetch), `private` (the bookmarklet steps), `not_found` (Yahoo's "There was a
problem" or "not found" page), `unrecognised` (some other page, or HTML claiming
a non-Yahoo URL), `unreachable`.

**Local test mode.** `ROSTER_SCRAPE_TEST` — on by default outside production —
ignores the typed ID and reads a completed public 2025-26 league
(`yahoo_rosters.TEST_ROSTERS_URL`, league 22705), so the scrape can be developed
against real markup with nobody's current league or sign-in. The page says so
when it is on. `ROSTER_SCRAPE_TEST=0` uses real IDs locally. The bookmarklet's
Yahoo link follows the same switch.

**Not yet verified end to end: the two-tab handoff.** The browser pane used to
build this will not open tabs from automated input, so the opener post, the ack
and the `?rosters=receive` path were each tested in halves — the bookmarklet on
Yahoo's real page, and the receiving page with messages from a Yahoo origin —
but never joined by a real bookmark click. That holds for every mode added
since (Matchup, Transactions, everything, bench lineups): each page-reading
step was run on Yahoo's live pages and each receiving step checked, never the
two joined. Check it by hand in desktop Chrome, with a private league, before
relying on it.

**Editing the page's JS through a shell heredoc mangles backslashes** — a regex
word boundary (backslash-b) became a literal backspace byte and shipped. Use the
Edit tool, or build the string with `chr(92)`, and check the file has no control
bytes with `grep -c $'\b'`.

### Scraping the score so far (`yahoo_matchup.py`)

**Scrape score from Yahoo**, on the Matchup tab beside the Opponent picker,
fills the score-so-far boxes from `/hockey/<league id>/matchup`. Typing them in
still works: the scrape opens the same boxes filled in, to check and edit.

**The League ID is entered once**, on the League tab, and kept in
`fs_yahooLeagueId` (saved as it is typed, not only on a roster scrape). Every
scrape reads it; a scrape with none sends the user to the League tab.

**Signed out, Yahoo shows team 1's matchup**, whatever league. A server fetch is
always signed out, so the request carries `mid1=<yahooTeamId>`, which the roster
scrape records per team — verified against a live league, where `mid1=3`
returned team 3's matchup with no sign-in. `week=` is the selected week's
number, and the parsed week is checked against it so one week's score is never
filed under another. A hand-entered league has no team numbers. It still
scrapes, but it gets team 1's matchup and is told so unless that happens to
include a team of the same name. A match by name records the numbers for next
time.

**The scrape also sets the opponent**, to whichever league team (by number,
then name) Yahoo shows opposite yours, and says so.

**What the page carries.** The score is the page's one `table.Datatable`: a
header of codes, one row per team (name linked to its team number), then the
categories won. A code ending `*` (GA*, SV*, SA*) is shown but not scored; Yahoo's
`SV%` is the engine's `SVpct`; a dash is nothing recorded yet, not zero. The
route adds each team's stats keyed by projection column, which is how
`fs_standaloneBanked` keys them. Only counting categories are banked — a rate
cannot be added to — and a category the page lacks is left as it was, with a
note. The live page (~3 MB after Yahoo's scripts) gives the same table as the
served one, checked in the browser.

Private leagues use the same bookmarklet as the rosters. It now accepts either
page and posts `fs-yahoo-page`, and the receiving page routes on the URL's path.
**A bookmark dragged before this still sends `fs-rosters` and still works for
rosters, but refuses a Matchup page, so drag it again.** Test mode reads week 2
of league 22705 from team 6's side (`TEST_MATCHUP_URL`), whatever week is picked.

### Season History: transactions (`yahoo_transactions.py`)

The first piece of the old site's Season History: every add, drop and trade in
the league, which its transaction pages and "left on the bench" views were
built from. **Scrape transactions** on the Season History tab; the result is
kept with the league in `fs_leagueTransactions` (so accounts sync it) and shown
as a per-team summary (adds, waiver claims, drops, trades) above the list,
filterable by team, week and kind. Selecting a team in the summary filters the
list. Times are shown and weeks cut in US Eastern.

**Public leagues come from Yahoo's public read-only API, not the page.**
`pub-api-ro` - anonymous, what Yahoo's own signed-out pages call, and already
used by the ADP scrape - answers `/league/nhl.l.<id>/transactions` for a public
league with structured JSON: epoch timestamps, team keys, and where each player
came from and went to. `start`/`count` page it (500 a request); a season is one.
On the completed test league it returned 1,136 transactions, the page's 1,132
plus 4 `commish` settings changes, which are dropped. A **private league answers
401** ("You must be logged in") and one that does not exist 400 (worded by
Yahoo as "a temporary problem"). Five of six imported leagues checked were
private, so the bookmarklet path is the common one, not the exception.

**The same API serves a public league's everything, checked 9/30/2026.**
`/league/nhl.l.<id>/settings` (categories with Yahoo stat ids, roster slots),
`/teams/roster` (each player's `status`, `status_full`, `injury_note`),
`/scoreboard`, `/standings`, and above all
`/league/nhl.l.<id>/teams/roster;date=D/players/stats;type=date;date=D` — **every
team's slot for each player on day D plus his stats that day, in one request**
(0.7 s, 1.3 MB; `-` for no game). That is the old site's "left on the bench"
data: on opening night it already showed two benched players who scored. A
season is one request per game day. Private leagues would need the equivalent
team pages by date through the bookmarklet — 12 a day — which is the harder half.

**Private leagues: the bookmarklet reads every Transactions page.** On a
`/transactions` page it fetches `?transactionsfilter=all&count=N` (25 a page)
from inside the signed-in Yahoo tab, keeps only each page's
`table.Tst-transaction-table`, and posts them with the browser's time zone to
`/api/transactions/parse`. Measured live on the test league: 46 pages, 1.4 MB,
30 seconds, with the tab title counting pages. **The bookmarklet now opens the
Fantasy Streams tab at the click**, not after reading - a tab opened once the
fetches finish is no longer a user gesture and is blocked as a pop-up. A
bookmark dragged before 9/29/2026 refuses Transactions pages; the steps say to
drag it again.

**The page parser was checked against the API on a whole season**: all 1,132
transactions agree at minute precision - types, players, from and to, all 11
trades and their picks, and years across New Year - and so does the live
league. Page rows: an "Added Player" / "Dropped Player" icon per player,
paired in order with a `div.Pbot-xs` whose `h6` says Free Agent, Waiver, To
Waivers or To Free Agent. **A trade is two rows**, the first with a trade icon
in a `rowspan=2` cell; each lists what one side received (players, and picks as
"Round N") and that side's team, whose link lacks `Tst-team-name`.

**Page times have no year or zone.** Signed out they are US Eastern - all 1,132
matched the API in America/New_York and no other zone. Signed in they are
presumably the user's zone, so the bookmarklet's browser zone is used;
**unverified**, for want of a private league to check. Years are inferred
walking back from the newest: a month-day later than the previous row's means
New Year was crossed. The anchor is today, or 1 July after a past season's
`/YYYY/hockey/` URL.

**The shape** (compact - a season is ~120 KB): `{league: {name, season}, teams:
{number: name}, players: {yahooId: [name, nhlTeam, positions]}, transactions:
[{id, type, time, moves: [[yahooId, from, to]], picks: [[round, from, to]]}]}`,
`from`/`to` a Yahoo team number or `freeagents`/`waivers`. Team numbers are the
roster scrape's `yahooTeamId`, and the page fills in teams Yahoo left unnamed
(the API only names teams that have made a move) from the roster scrape.
Players carry Yahoo ids only; **matching them to projections is for whatever
needs their stats next**, with `yahoo_rosters.match`.

**Phone:** the list is four columns on a desktop and one stacked cell on a
phone (`.wide-only` / `.narrow-only` in `styles.css` - not `sm:` utilities,
which this page's markup never uses and the CDN build would not generate).

Test mode reads the completed 2025-26 league 22705, which turns out to be last
season's copy of league 5848; weeks fall back to Monday-Sunday there, since
fantasy weeks are loaded for the current season only.

### Season History: left on the bench (`bench_points.py`, `yahoo_league_api.py`)

The old site's bench points: what players scored while sitting on a bench,
**all season** (a league table of bench games and the counting categories they
left behind) and **week by week** (a team's bench appearances, the league's week
at a glance, and — the salt in the wound — each single swap that would have won
or tied a category, with what it would have cost). It loads when Season History
is first opened, and as the fourth step of Update from Yahoo.

**Public leagues: Yahoo's public API** (`/api/bench`). One request per game
day, `/league/<key>/teams/roster;date=D/players/stats;type=date;date=D`, gives
every team's slot for each player and his stats that day.

**Private leagues: the pages for lineups, our own game data for stats**
(`/api/bench/lineups`, `bench_lineups.py`). Reading each team's page by date
would be 12 pages (~1 MB each) a game day, so instead:

- **Lineups** from **Starting Rosters by date** (`startingrosters?date=D`):
  every team's slot for every player that day, with each player's NHL team and
  positions. Checked on league 5848: identical to the API for all 202 players.
  The bookmarklet reads the missing days (three pages at a time) and reduces
  each to `[yahooId, slot, name, 'TEAM - POS']` in the Yahoo tab — ~8 KB a day.
- **Pairings** from League Home by week (`?matchup_week=N&module=matchups`),
  whose matchup links carry `mid1` and `mid2`.
- **Stats** from `player_game_stats`, the same NHL feed Yahoo scores from.
  Players are matched by name, the page's team and positions settling
  look-alikes — every one of 456 in a season matched, with no call to Yahoo.
- **Week totals are summed from each day's starters**, which is how Yahoo keeps
  them; GAA from goals against over the goalies' own seconds.

**Checked against the API on a whole season** (the test league is public, so
both routes can read it): bench games **1,178 on both, identical per team**, and
season bench totals identical. Per player per day the game data matched Yahoo's
own stats bar one block all season. 294 of the API's 326 swap outcomes come out
the same; the rest sit on a category decided by one shot, where Yahoo's week
totals and its own daily stats disagree by one (stat corrections) — summing
Yahoo's daily stats reproduces the same gap.

**The lineups live on the device** (`fs_benchLineups`: `{league, season,
private, dates: {date: {team: [[yahooId, slot]]}}, players, pairs}`), not in an
account — ~3 KB a day is too much to sync with every edit, and they can be read
from Yahoo again. Update from Yahoo's bookmarklet link asks for exactly what is
missing (`#fs-all&bench=FROM..TO&weeks=A-B`: every day from the first missing
to yesterday, and the pairings of weeks begun without any), so a season is
caught up once (~180 pages, about a minute at three at a time) and a day at a
time after. A date whose page came back without rosters is not kept, so it is
asked for again. A league the server has found private is marked so, and goes
straight to its stored lineups next time.

**Every Yahoo category the NHL game data carries is covered**: faceoffs (from
`skater/faceoffwins`, which the nightly scrape now collects — see *Per-game
results*), time on ice in minutes (Yahoo's skater TOI, id 33), and shooting
percentage, rebuilt from goals over shots like the other ratios. Only
game-tying goals, which no NHL report carries, is reported as unsupported;
unmatched players are listed. A game row scraped before faceoffs were collected
has none — "not collected", not zero.

**`yahoo_league_api` caches in `yahoo_public_cache`**, keyed by Yahoo's league
key, and only what can no longer change: a day before today (US Eastern), a
week whose scoreboard says `postevent`; settings for six hours. So a season is
read from Yahoo once, whoever asks: a whole completed season (180 days, 23
weeks) took 47 s the first time and 0.4 s after. Public data only — nothing a
private league sends is ever stored there. Days are read four at a time.

**What counts.** A bench appearance is a BN slot on a day the player has stats
(Yahoo's `-` is no game). IR, IR+ and NA never count: he could not have played.
Season totals are counting categories only — GAA and SV% cannot be added up —
and display-only categories (`is_only_display_stat`, often GA, SV, SA) are not
scored at all.

**A swap is one bench player for one starter on one day** — "played X over Y".
X must fit Y's slot: listed there, or a generic he fits (F any forward, W a
winger, Util any skater; a goalie only for a goalie). A starter with no game
that day is included and marked — the cheapest swap there is. The week's totals
are Yahoo's own from the scoreboard; a swap takes Y's line off and puts X's on,
and every scored category is compared with the opponent again. Kept if it wins
or ties at least one more category, with any it would lose shown beside it.
Starters giving the same outcome are one line ("over Fox, Jones or Gavrikov"),
and the best two outcomes per appearance are kept.

- **Ratios are rebuilt, not skipped.** SV% from saves over shots against. GAA
  from goals against over minutes, where a goalie's minutes come back out of
  his own `GA x 60 / GAA` (a shutout counts 60) and the team's out of its week
  GA and GAA. If a league does not carry the counts, the ratio is left as it
  was rather than guessed.
- **A counting category nobody has recorded yet is zero** (Yahoo's dash); a
  ratio with nothing under it decides nothing.
- **A week in progress is judged on the score so far**, and says so.
- **Points leagues** get the bench totals but no swaps — they are decided on
  fantasy points, which this does not score yet.

The response is ~200 KB for a season (lines carry only non-zero stats) and is
held in the page, not stored — the server's cache makes it quick to fetch again.

### Update from Yahoo (every scrape at once)

**Update from Yahoo**, in League Home's settings bar, runs every scrape in the
order they depend on each other: rosters (they carry each team's Yahoo number),
the score so far (which needs yours), transactions, then bench points (for a
private league, from the lineups the bookmarklet reads in the same click).
Results show per step
in `#update-panel`. `UPDATE_STEPS` in the page is the list — **a future scrape
joins by adding a step there** and, for private leagues, a page to the
bookmarklet's everything mode. It is shown to everyone, signed in or not.

- **Rosters apply without the preview when that is safe** — your team is
  already known by its Yahoo number (`autoApplyRosters`). The first import of a
  league, or one entered by hand, still gets the preview to ask which team is
  yours, and the score is skipped until it knows (signed out, Yahoo would show
  team 1's matchup). The opponent follows by Yahoo number too, and the score
  step resets it from the matchup anyway.
- **Public leagues are read by the server** (the three `/scrape` routes).
- **Private leagues: one bookmark click reads everything.** The first `private`
  answer hands over to the bookmarklet steps, whose link opens the league's
  Starting Rosters page with `#fs-all&week=N`. That marker — or being on the
  league's home page (`/hockey/<id>`) — puts the bookmarklet in everything mode:
  it fetches `startingrosters`, `matchup?week=N` (the signed-in user's own) and
  every transactions page in the Yahoo tab, and posts one `fs-yahoo-all`
  message with each page's HTML and address. The receiving page runs the same
  steps through the `/parse` routes. Checked on league 5848's live home page:
  12 rosters with 15 status badges, the week's matchup table, the transactions.
  **Bookmarks dragged before 9/30/2026 cannot do this** — the steps say to drag
  again.

### The player card (`player_card.py`, `season_stats.py`, `game_lines.py`)

**One modal for every player League Home lists**, in place of the old site's
five (`Interestingkiwi/fantasy-streams`, `static/home.js`): a last-game line
pill, a goalie-starts pill, a trend table, a PP modal and an opponents modal,
each opened from its own cell. Here a name opens the card on its stats, and
the roster table's trend, PP% and opponents cells and the `L1 · PP1` badge open
it on their own tab, as those modals did. Names are tappable in the Lineups
roster view, the Free Agents table and recommendations, the League tab's
rosters, the nightly grid, Season History's transactions and bench lists, and
inside the card itself (a linemate opens his card). Full screen below `sm`;
checked at 375px. The modal sits at `z-50`, above the other modals, since a
name inside one opens it.

`GET /standalone/api/player/<playerId>?start=&end=` builds it (`player_card.card`,
pure; the route loads rows). It needs no league: the page orders the stats by
the league's categories and formats them. `start`/`end` are the selected
fantasy week, for the Schedule tab.

- **Stats & trends** — his season to date and the same stats over his last
  20, 10 and 5 games (`player_form.TREND_WINDOWS`) and at home and on the road,
  with a **Totals / Per game** toggle and his projection beside the season
  (per game, or over the same games in totals). League categories first, then
  the rest by group; a stat the league does not score is hidden while it is
  nothing in every column. A window appears once he has played that many
  games. Arrows are `player_form.trend`'s standard-error test **per stat**,
  counting stats only; a ratio over five games is mostly noise.
- **Game log** — his last 10 games in the league's categories, ice time and
  PP time, with his line and unit each night.
- **Line & PP** (skaters) — last game's line, linemates and their time
  together at 5-on-5, who else he skated with, his PP unit with its mates and
  his share of the power play, then game by game.
- **Starts** (goalies) — his starts against his team's last 10 games and the
  season, the rest he would have for the next game (back-to-back flagged), and
  his record by rest and venue. Splits carry their game counts; the card says
  how little a handful of starts means, and that start odds come from the
  team's projections, not from these.
- **Schedule** — his team's games in the week, the plan's start/bench/out for
  each if he is in the plan, and each opponent's season and last-two-weeks
  goals and shots allowed and penalty kill (a goalie: goals and shots for and
  power play), each ranked so 1 is the kindest to him — for a goalie the
  fewest goals but the most shots, since shots are saves.

**Every stat in `season_stats` is a definition**: how to read one game row,
and whether it is counted or a ratio. A ratio — shooting, faceoff and save
percentage, GAA, PP share — is rebuilt from its summed parts over the window,
never averaged game by game, and a goalie's GAA is over his own seconds. A
stat whose column a window's games were scraped without is None, not zero.

**The injury feed is named for what it is.** The card shows the preseason ESPN
feed's status with its date and says when he has played since — Matthews was
"Out, back 9/15" in the feed while playing both opening nights.

#### Lines from shift charts (`game_lines.py`)

The one piece that needed a new data source. The old site worked lines out of
`api.nhle.com/stats/rest/en/shiftcharts` (every shift of a game: who, which
team, which period, from when to when) by summing shared ice time at every
strength, and called a team's top five by PP time PP1. Here every shift is
laid onto a per-second grid, so the **strength is counted** — the chart does
not say it:

- **Lines are 5-on-5 time together only.** Both sides at five skaters; an empty
  net (six against five) is neither 5-on-5 nor a power play. Every trio of
  forwards and pair of defencemen is scored by pairwise shared seconds and taken
  greedily — four lines, three pairs — and a group whose weakest pair shared
  under two minutes is not a line. The extra skater on an 11-and-7 night ends
  with no line, but still with the teammates he played most with.
- **Numbered by offence, not by time together.** Line 1 is the group whose
  members have the most 5-on-5 plus power-play time — a shutdown pair can lead
  its team in time together. Measured against Daily Faceoff: numbering by time
  together put 30 of 51 forward lines and 16 of 42 pairs in their slot, by
  offensive time 39 and 23.
- **PP units are power-play time together**: PP1 is the player with the most
  of it and the four who shared most with him, PP2 the same from those left. A
  team with under a minute of power play that night has no units, and the badge
  reads the unit from his last game that had a power play.

**Checked against Daily Faceoff** for all 16 teams that had played by
10/1/2026, both reading the same last game: the same groups for 52 of 59
forward lines, 42 of 45 pairs and 25 of 30 PP units, PP units in the same slot
24 times. Toronto matched exactly but for lines 3 and 4 swapped. Their pair
order looks partly editorial, so line numbers will keep differing there.
Scoring trios by the time all three were on at once grouped no better (111 of
125) and split an 11-forward night differently from them.

Only the result is stored — one row per skater per game in `player_game_lines`
(line, linemates, time together, top 5-on-5 mates, PP unit and mates, his and
his team's PP seconds), not the ~730 shifts behind it. The nightly job fills
any game in the look-back without lines, so a chart not posted yet is tried
again the next night; `python game_lines.py --start --end` backfills.
`game_lines.latest()` gives the roster badge; `recent()` the card.

### Stat Sourcing (`stat_sourcing.py`)

The settings bar's **Stat Sourcing** chooses what players are valued on:
**Projected** (the preseason projections), **Season to date** (each player's
rate so far, per game, per start for a goalie), and **Combined** (not built).
The choice is `fs_statSource`, per device, and rides in `planBody` as
`source`, so the plan, the free agent pool and search, and goalie planning all
take it. An unknown source is a 400, never a silent fallback.

**The seam is a projection-shaped row.** `season_rows()` rewrites each
`final_projections` row so its counting columns are his season rate times his
projected games (starts, for a goalie) — exactly what `daily_value` divides
by, so it reads his rate back and nothing downstream knows the difference.
Totals come from one SQL aggregate (`season_sums`) per request; a goalie's are
over his **starts only**, since a relief outing is not a start. Projected
games and starts are kept: goalie start odds are balanced against them.

Three things deliberately stay on the projections:

- **The category weights.** `Week(values=...)` values players on the source's
  rows but takes σ from `pool`, the projections — a few weeks' rates spread
  far wider than the true ones and would re-weight the league.
- **The draft board's rank** (`seasonRank`, the drop suggestions): it prices
  the rest of a season. `_season_values` reads `week.projections`.
- **A player with no games** keeps his projected line, marked
  `statSource: 'projection'`; the roster view's GP column shows `proj` for him
  and his games for everyone else. An injured starter would otherwise be
  planned at nothing.

**It opens itself.** Not on opening night — a rate over a game or two is
noise a lineup would chase — but the day after every NHL team has played
`OPEN_AFTER_TEAM_GAMES` (5) games, read from `nhl_schedule` like the nightly
job's season gate: **2026-10-14** for 2026-27 (four games would have been the
11th, six the 18th). Before then the option is greyed out with its date and
the routes refuse it with that date. **`STAT_SOURCING_PREVIEW`** (config; on
everywhere but production) opens it early, labelled as a preview, for
building and checking it. Nothing needs switching on in October: Render's
page offers it on the 14th by itself.

## Accounts (temporary)

With Yahoo's API gated for the season, every league is scraped or typed in by
hand and lived only in one browser's localStorage. An account (the button at
the end of the nav; first on a phone) keeps each user's leagues server-side so
they follow them between devices. **Username and password only** — no email,
so no reset; the form says so. `manage_accounts.py` is how the developer lists
and deletes accounts (someone who lost a password and started again).
**Retire all of it once Yahoo sync is live** — see *When Yahoo API access is
granted*.

**Per account, never shared.** Each account holds its own copy of each league
in `account_leagues` (one JSONB `state` of `{localStorage key: raw string}`),
so a Yahoo league is stored once per member who saves it. One shared row per
Yahoo league, joined by scraping it, was considered and rejected: a private
league arrives as HTML the user's browser posts through the bookmarklet, and
the server cannot tell a real Yahoo page from a hand-made one with the right
league ID — so scraping proves nothing, and sharing would expose a private
league to anyone who knew its ID. Per account needs no verification at all.

**The pages were not changed to use it.** They still read and write
localStorage. `partials/account.html` (included by `page-nav.html`, so on every
page with the nav) renders the open league's state into `window.FS_ACCOUNT`,
and `static/account-sync.js`, running before any page script, writes it over
the browser's copy. It then wraps `Storage.prototype.setItem`/`removeItem`, and
any write to a league key saves the league 1.5s later. That is why removal is a
handful of files rather than edits through three pages.

- **Which keys.** `LEAGUE_KEYS`, listed identically in `account_routes.py` and
  `account-sync.js` (a test fails if they drift): teams, Yahoo league ID,
  scoring, roster and lineup slots, playoff weeks, fantasy weeks, moves, score
  so far, goalie stats, manual lineups, scraped transactions. Per-device state stays local: open tab,
  selected week, *Only nights still to play*, and draft prep's view settings,
  saved lists and tags (the draft is over; lists are ~1 MB).
- **Unsaved edits win.** A write sets `fs_accountPending` until a save carrying
  it lands; a page opened while it is still set sends the local copy up instead
  of overwriting it (a save lost to a closed tab or a dropped connection). A
  failed save shows a red dot on the account button.
- **But never over a newer copy.** Saves replace the whole league, so each
  carries `base`, the `updatedAt` its copy came from, and the server refuses
  (409) one built on an older version. The page says so and reloads onto the
  newer copy. A tab coming back into view asks `/version` and reloads onto a
  newer copy if it has nothing unsaved. Without this, a desktop tab left open
  overnight would silently undo the lineups set on a phone the next time it
  saved anything.
- **Whose copy is this?** `fs_accountOwner` records the account and league the
  browser's keys came from. On sign-in, keys with no owner (entered signed out)
  are offered to the account as another league — silently when the account has
  none. Keys owned by a *different* account are never carried in.
  Signing out clears the league keys.
- **Staying signed in** is the permanent Flask session cookie
  (`PERMANENT_SESSION_LIFETIME`, 30 days, renewed each visit). It carries only
  the account id, and every request re-reads the account, so one deleted by
  `manage_accounts.py` is signed out everywhere.
- **Limits.** Usernames 3–30 of `[A-Za-z0-9_.-]`, unique ignoring case;
  passwords 8+ (Werkzeug scrypt hash). Ten wrong passwords lock the account for
  15 minutes (in the table, so it holds across workers). Five sign-ups per
  address per hour, keyed on the *last* `X-Forwarded-For` hop (Render's; the
  rest is client-supplied). 20 leagues and 1 MB per league (a season of
  transactions is ~120 KB; the busiest imported league would be ~250 KB). Every
  save sends the whole league, transactions included, which is also more than
  `keepalive`'s 64 KB - a save on a closing tab is refused and the pending
  flag sends it next time. Every write
  endpoint requires JSON, which a cross-site form cannot send without a
  preflight; SameSite=Lax on the cookie does the rest of CSRF.
- **A database problem never costs the page** — `boot()` degrades to signed
  out and the page runs from localStorage as before.

Checked in the browser end to end: sign-up carrying a signed-out league, an
edit reaching the server, a wiped browser getting the league back, a stale tab
refused and reloaded, a hidden tab reloading on focus, a lost save sent up on
the next load, new/open/delete league, sign-out clearing, sign-in offering the
browser's league, deleting the account, and the modal full screen at 375px.
**Not checked: two real devices.** The version check was exercised by saving
from the same browser as a stand-in for the phone.

## Draft prep page

Everything except the ranking-method control lives in the **League Settings** modal:
league structure (categories/points + number of teams), roster settings, playoff weeks,
and the skater/goalie category grids. **Rank Via** (Roster Setting / Projection Only /
Balanced) sits on the page itself, in the filter bar, and re-ranks on click.

State is `localStorage`, all keys prefixed `fs_`: `fs_selectedStats`, `fs_statWeights`,
`fs_leagueMode`, `fs_pimPolarity`, `fs_numTeams`, `fs_rosterMode`, `fs_rosterSlots`,
`fs_playoffWeeks`, `fs_rankMode`, `fs_heatmap`, `fs_hiddenColumns`, `fs_condenseGoalies`, `fs_lists` (plus `fantasy_streams_tags`
for player tags).

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

**Heat cells are opaque, deliberately.** The Favourite/Sleeper/DND row tints are
translucent Tailwind backgrounds, so while the heat cells were translucent too
the tag colour showed through and shifted the shade — two players with the same
number read as different values depending on whether one was tagged, which is
exactly what a pool-wide scale exists to prevent. `heatStyle()` composites the
tint down onto the table surface, and paints an untinted cell the surface colour
rather than leaving it transparent. The tag still marks its row through the name
columns and the coloured left edge.

**Position filter** carries two group filters alongside the five positions:
`SKATERS` (eligible somewhere other than G) and `FWD` (C/LW/RW).
`matchesPosition()` asks a question about the whole eligibility list rather than
looking for one entry in it, which is what separates them from `C` or `D`.

### Saved lists and export

**Create List** snapshots the current view — rows as filtered and sorted, plus
the columns, the heat scale and the polarity behind them — as its own tab. A
snapshot is frozen, not a saved filter: a sleepers or peripherals list has to
keep saying the same thing in round 12 as it did in round 1, whatever the board
has been re-ranked into since. Re-ranking therefore returns the view to the Main
Board rather than leaving a stale-looking list on screen. The filter bar still
narrows whichever tab is open, so a list stays searchable without being editable.

A snapshot keeps `positionCode`, `age`, `projectedGames` and `adp` as well as
the visible columns: the three are toggleable, so a list that dropped them would
show blanks the moment one was un-hidden on it, and `positionCode` is how
Condense Goalies tells a goalie from a skater. Lists from before those fields
were kept fall back to `eligiblePositions` for that, and show `-` for the rest.

Snapshot rows are the same shape as live player rows, so filtering, sorting,
tagging and rendering are one code path for both; `viewConfig()` is the single
place that decides which of the two is on screen. They are **stored as a field
list plus one array per player**, not as objects repeating 20 key names each —
the keys were over half the bytes, and ten full-board snapshots as objects came
to ~4 MB against a 5 MB quota. Compact they are ~1.1 MB, which is what makes
`MAX_LISTS = 10` safe. A failed write is reported, never swallowed.

**Export List** (`/api/export`, openpyxl) always exports the whole active list —
every row, filters and pagination ignored. Filters are how you build a list; the
list is what gets exported. The page sends the values and the cell colours it is
already showing rather than a description of the settings behind them: the heat
scale, the tags and the polarity all live in `localStorage`, and re-deriving them
server-side would be a second implementation of the same maths that could only
drift from the first. So the route knows nothing about hockey — it turns a grid
of values and `RRGGBB` fills into a workbook. Excel has no translucency, so the
page composites each fill over **white** for the export where it composites over
the dark surface for the screen.

**Age, Proj GP and ADP** sit between Pos and the value columns. Age and
projected games already existed on `final_projections`; ADP is joined on. All
three are display-only.

**Column toggles** drop groups of columns: Trends, Age, GP, Schedule
(Light + Playoff) and ADP. **They are per view.** The Main Board keeps its
settings in `fs_hiddenColumns` / `fs_condenseGoalies`; every saved list carries
its own `hiddenColumns` and `condenseGoalies` inside its entry in `fs_lists`, so
stacking or hiding in one list leaves the board and every other list alone. A new
list starts with the settings of the view it was taken from. Everything reads
through `viewColumnSettings()`, which is what keeps a list and the board from
ever reading each other's. Lists saved before this took the board's settings
once, on first load, and were written back so they stopped following it. They sit with the
list tabs rather than in the filter bar, because they describe the shape of the
table directly below them, not which players are in it — filters narrow the
rows, these drop columns. Pressed means hidden, which is why the pressed state is
the muted one and every label starts with "Hide". They re-render rather than re-rank — hiding a column changes
nothing about the maths, and a hidden column stops being the sort column so the
board cannot be ordered by something invisible.

**Condense Goalies** stacks the goalie categories onto the skater ones: the
first goalie category shares a column with the first skater category, the second
with the second. A league scoring G/A/P and W/GAA comes out three columns wide —
Goals/Wins, Assists/GAA and Points, the last simply blank for a goalie. In the
export that blank is an empty cell rather than a `0`, which would read as "zero
hits" instead of "not applicable". Sorting a shared column has to mean one
thing, so it sorts on the skater category and goalies fall to the bottom. It is
a toggle because a heading naming two different stats is confusing to some
readers and much narrower to print for others.

**The header row is sticky.** It needs a real scroll container to stick to, and
`overflow-x: auto` alone makes one of unbounded height — so the table scrolls
inside a viewport-height box in both axes (`.table-scroll`), which also keeps
the filter bar and pager on screen. The `th` cells carry their own background,
because a sticky cell shows the rows through it otherwise.

**One list describes the columns.** `columnSpecs()` returns each column's label,
sort key, export kind *and* its `cell()` renderer, and the header, every table
cell, the export and saved lists all read it. The body used to re-list the
columns by hand in `buildTableBody()`, which meant adding a column meant editing
two sequences that had to agree; a column can no longer exist in the header and
be missing from the body.

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
follow `fantasy-weeks.js` (see *Fantasy weeks*); the date ranges live in
`data-start`/`data-end` on checkboxes built from the season's last five weeks.

**In the export that column becomes `10(3)`** — ten playoff games, three of them
on light nights. The screen carries the light count in a smaller suffix and a
tooltip; a spreadsheet cell has neither, and the light figure is half the reason
to look at the column. It costs the cell its numeric sort, which is the trade.
The column spec carries a `lightKey` that only `exportValue` reads (`kind:
'playoff'`), and it falls back to the bare game count rather than writing
`10(null)` if the light figure is ever missing.

### On a phone

Below Tailwind's `sm` breakpoint (640px) the page rearranges rather than just
shrinking. Drafts happen on phones, and before this the board was a 454px-wide
page on a 375px screen, pushed out by the nav.

- **The Player column is frozen** (`.col-sticky`, at every width): a row of
  numbers means nothing once the name has scrolled off. Being opaque, it cannot
  let the row's translucent tag tint through, so it paints the tint itself off
  `tr[data-tag]`, pre-composited over the surface — the same reasoning as the
  opaque heat cells. Change a tag colour and those three rules in `styles.css`
  have to follow it.
- **The tag buttons move to a bottom selection bar** (`#selection-bar`), shown
  only while a row is ticked, because up top they sit a screen away from the
  rows. A tap anywhere in the select cell toggles its checkbox
  (`toggleFromCell`), dispatched as a `change` so select-all and the bar hear it
  as a real tick.
- **Column toggles fold behind one Columns button**, list tabs become one
  sideways-scrolling row, Rank Via drops to short labels, and League Settings
  goes full screen with Apply pinned at the bottom.
The general rules this page set — header, spacing, `.data-table`, 16px inputs,
the two Tailwind traps — apply to every page and live in *Phone layout (every
page)*.

## The projection pipeline (`preseason_db_build/`)

`build_database.py` runs the steps in order via `subprocess`. Roughly:

1. `add_tables.py` — schema/logs
1b. `scrape_nhl_schedule.py` — every team's regular season from `api-web.nhle.com`
   -> `nhl_schedule`. Runs early because the projection steps pace against its
   season length. Reads the season from the API rather than hardcoding dates.
2. `historic_data_skaters.py` / `historic_data_goalies.py` — NHL API season stats,
   plus `birthDate` from the `bios` report so the projections can tell how old
   each of those seasons was played at. Paged with an explicit `sort` — see
   *Paging* below; without one the same run returned a different set of players
   each time.
3. `append_advanced_skaters.py` / `append_advanced_goalies.py` — MoneyPuck advanced stats
4. `create_player_directory.py`
5. `scrape_ep_rookies.py` (EliteProspects) / `enrich_ahl_stats.py` (HockeyTech feed)
6. `scrape_injuries.py` (ESPN injuries API)
7. `calculate_skater_projections.py` / `calculate_goalie_projections.py` —
   60/30/10 time-decay weighting of the last 3 seasons (per-game), paced to the
   season length from `season_config`, with production & peripheral trend labels;
   40-game 3-yr minimum, 10-game per-season minimum. Each skater season is first
   restated at next season's age — see *Ageing* below. Goalie rates are regressed
   toward the league mean by sample size (`REGRESSION_GAMES`), which stops a
   40-game backup out-projecting established starters on rate.
8. `apply_injury_adjustments.py` — final downward adjustments -> `final_projections`.
   Opening night and the pace of play come from `season_config` (`season_start_date()`,
   `days_per_game()`), not from constants in the file — both hardcoded values had gone
   stale against 2026-27 and pulled opposite ways: 8 October was nine days after the
   real opener, while dividing days-missed by 2 assumed a game every other day when 84
   games over that calendar is one every 2.30. **It also prints how old the injury feed
   is and warns past a week** — a stale feed fails silently and convincingly, since
   everyone still gets a projection and the only symptom is that a player hurt last week
   looks healthy. Bedard was the real case: the feed was two months old, his shoulder was
   reported the day before, and he ranked as a fit 21-year-old. Re-run
   `scrape_injuries.py` (step 6) before trusting a board near a draft.
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
13. `scrape_yahoo_adp.py` — current ADP from Yahoo into `player_adp` (see *ADP*).
   **Last, and it has to be:** the crosswalk matches against `final_projections`,
   so it needs the rookies added at step 9, the roster pruned at step 11, and
   above all the current teams written at step 10 — Yahoo reports where a player
   is *now*, and team is one of the matching passes. Also runnable on its own,
   which is the usual way: ADP moves daily and a refresh costs three requests
   rather than a rebuild.

**Re-running part of the pipeline:** `apply_injury_adjustments.py` rebuilds
`final_projections` with `to_sql(if_exists='replace')`, which drops
`eligiblePositions`. Any re-run from step 8 must carry on through step 12.

External data sources: `api.nhle.com`, `api-web.nhle.com`, `moneypuck.com`,
`eliteprospects.com`, `lscluster.hockeytech.com`, `site.api.espn.com`.

### Ageing (`aging.py`)

The 60/30/10 blend used to treat three seasons as interchangeable, which assumes
a player is the same player at 38 as he was at 36. So it leant a fading veteran
up and a rising kid down, hardest on the seasons furthest from the one being
projected. Ovechkin was the tell: his age-39 record chase carried 30% weight at
face value and put a 41-year-old at 72 points and rank 53, against an ADP near 100.

Each historical season is now restated as what it would be worth **at next
season's age** before it is weighted. It is a re-basing, not a haircut — the same
curve moves a 20-year-old's older seasons *up*, and leaves a 26-year-old's alone.

The curve is measured off `historic_skaters_baseline` by `derive_aging_curve.py`
— the delta method, each player against himself across consecutive seasons,
weighted by the harmonic mean of the two game counts. Over 2,388 pairs the
year-over-year scoring ratio is a straight line in log-age from 24 to 38: ~5.5%
lost a year at 30, 10% at 35, 14% at 40. **Peripherals decline about half as
fast** (2% at 30, 7% at 40) and get their own curve — hits and blocks are usage,
not burst, and usage survives. Growth before 24 does not fit that line and is
read off the measurements instead (~11% a year at 20-21).

**Only half the growth is credited (`GROWTH_CONFIDENCE = 0.5`); decline is
applied in full.** This is the one number in `aging.py` that is a judgement call
rather than a measurement, and it is set that way on purpose — at full strength
the curve moved the median 19-year-old up 118 ranks, which is a bigger claim than
the evidence behind it. Two things were checked first, and only one supports
damping: growth *is* mildly right-skewed (median/pooled 0.951 at ages 19-23
against 0.970 at 26-32, so the pooled ratio is pulled up by breakouts), but
growth is **not** more variable than decline (sd of the year-over-year point
ratio is 0.417 at 19-23, 0.411 at 26-32, 0.386 at 34-39 — flat), which is the
argument that would have justified shrinking it harder and does not survive
measurement. The rest is deliberate conservatism about the thinnest part of the
model: the growth ratios rest on 5-191 pairs against ~200 a year through the
decline, they compound hardest over the seasons furthest back, and 24 of the
players they lift have only one NHL season to lift.

Damping deliberately breaks the curve's symmetry — `age_factor(30,34) *
age_factor(34,30)` is no longer 1 — because crediting a young player up is a
weaker claim than marking an old one down. `test_aging.py` pins that, and pins
the seam at 0.0 and 1.0 so the dial keeps working either way.

Three things worth knowing before touching it:

- **`plusMinus` is never scaled.** It is signed, so multiplying a negative one by
  0.85 would read as the player improving. It is the one projected stat with no
  curve, and `test_aging.py` fails if that list drifts.
- **The tail is extrapolated on purpose.** Past 35 the measured decline flattens,
  but so does the sample: year-over-year survival falls from ~85% to 56-68%, so
  what is left is the players who held up. The fitted line is carried through the
  tail rather than the n=5 buckets being believed.
- **Games played carries no age penalty.** Among skaters who were regulars, mean
  games the following season does not fall with age — it sits between 63 and 70
  from 21 through 38, because the old players still in the league are the durable
  ones. There is no effect in the data to apply, and `projectedGames` already
  reads durability off each player's own history.

**Goalies are not aged.** The same measurement on `historic_goalies_baseline`
returns a save percentage falling ~4 points a year at *every* age from 24 to 36 —
that is the league-wide save percentage decline over these seasons, not ageing,
and 230 pairs cannot separate the two. Where age reaches a goalie is his
workload, which already comes from `goalie_gp_overrides.csv`. Both projection
tables still carry an `age` column, so `final_projections` is uniform and the
adjustment is auditable; the ~39 imported rookies have no NHL bios row and so no
age, and are projected unadjusted.

The curve is **not re-derived at build time**. `derive_aging_curve.py` prints
coefficients to copy into `aging.py` by hand — a curve that quietly moved every
time a season landed would make two runs of the same projection incomparable, and
the tail is thin enough to deserve a person looking at it.

**League total.** Re-basing takes ~4% of counting stats out of the projected
pool, because the pool is older than peak age on average. That is real — the
production goes to players with no three-year history, who are not in this table
— and it does not touch the ranking, which is relative. Skater totals move and
goalie totals do not, which is the one place the two sides shift against each
other.

### ADP (`scrape_yahoo_adp.py`)

Where the market has a player, against where this board does. Written to
`player_adp` and **left out of `final_projections` on purpose**: ADP is the one
number here that keeps moving, since every mock and every real draft between now
and yours changes it. As its own table it is refreshed by one script in about
ten seconds, at any hour, without rebuilding a projection or re-running steps
8-12. `draft_routes` LEFT JOINs it on at read time, and only when the table
exists, so a database that has never run the scraper still serves a full board.

**It is not a page scrape, despite appearances.** `/hockey/draftanalysis`
renders client-side and paginates without touching the URL, so scraping the page
would mean driving a browser and watching rows change. It does not have to: that
page feeds off `pub-api-ro.fantasysports.yahoo.com`, Yahoo's *public read-only*
fantasy API, which takes `start`, `count` and `sort` as ordinary parameters and
answers plain `requests` with no auth, no crumb and no login. Confirmed by the
resource timeline — the HTML carries no player data at all, for a browser or
anything else.

This is emphatically **not** the gated `pub-api-rw` API that Phase 2 is waiting
on. It is read-only, anonymous, and is what the public page serves visitors
with.

- **Sorted by `average_pick`**, so it arrives best-pick-first and the players
  nobody drafts collect at the end carrying `"-"` rather than a number. That
  dash is the stopping signal: the first one means everything after it is
  undrafted too. ~265 players have a real preseason ADP.
- **The season is read from `/game/nhl`.** Yahoo keys each season with a game id
  (477 is 2026-27); hardcoding it would quietly scrape last season a year from now.
- **Yahoo uses its own player ids**, so every row is crosswalked onto the NHL
  `playerId` by name, then team, then position. The position pass is not a
  nicety: Vancouver carries two Elias Petterssons, a centre and a defenceman, and
  name plus team cannot separate them. Matching is against `final_projections`
  rather than `player_directory` — the directory is built at step 4, before the
  rookies are added at step 9, so matching on it silently lost every prospect who
  has an ADP and no NHL history.
- **Unmatched rows are stored with a null `playerId` and listed at the end**, so
  a miss is visible rather than absent. `player_utils.add_player_alias()` fixes a
  genuine one.

It runs as step 13 of `build_database.py` too, so a full rebuild refreshes ADP
along with everything else — the standalone script is the *fast* path, not the
only one.

Copy it up on its own near a draft:
`python transfer_to_render.py --tables player_adp --apply`.

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
| `test_opponent_strength.py` | mean-neutrality per category, the per-category directions (including the two goalie ones that oppose each other), that the adjustment breaks ties without reordering tiers, and home ice held at its long-run size until a season's games earn their weight — an opening night read raw is wild, blended it barely moves |
| `test_game_results.py` | the per-game scraper against a stubbed API — paging, weekly chunking, and above all that hitting the 10,000-row ceiling raises instead of truncating quietly; faceoffs from the fifth report landing on the right rows, never zeroed where the report is silent; the card's later columns collected, added to an older table, and on the list a gap re-scrapes - team PP time not |
| `test_nightly.py` | the season gate: silent before opening night, live from it, and standing down cleanly rather than failing when no schedule is loaded; the missing-column look-back each night, and lines last |
| `test_game_lines.py` | lines from a game built shift by shift: 5-on-5 and power-play seconds counted from the skaters on the ice (an empty net is neither, goalies never in a set), line 1 the offensive line even when the checking line is out longer together, PP units from shared power-play time, a skater with no position on no line, the badge's unit from the last game with a power play; then that every game this season has lines and no line or unit is oversized |
| `test_player_card.py` | the card with no database for the maths: ratios rebuilt from summed parts (and a goalie's GAA over his own seconds), an uncollected column missing rather than zero, windows only once played and arrows by `player_form`'s test on counting stats only, a goalie's starts against scraped team games with rest since his last appearance, opponent ranks kindest-first (a goalie's shots the other way); then the route's 404, 400 and both kinds of card |
| `test_stat_sourcing.py` | Stat Sourcing: opening the day after the last team's fifth game (and the preview opening it early), rewritten rows reading back through `daily_value` as the season rate (per start, GAA over seconds), no-game players kept on their projection, no goalie column on a skater, weights and draft-board rank left on the projections; then a goalie's relief outings left out of the sums, and the routes - planning with it, refusing it before it opens with the date, and refusing an unknown source |
| `test_week_planner.py` | standalone mode: out players counted but unseated, idle nights, backup-only goalie odds balanced over the whole team, points values; against an opponent, odds that follow the margin (inverse categories, banked deficits, points), and a lineup that starts the grinder over the sniper once goals are lost; planned moves by date; free agents — rostered players never suggested, drops from season value and never an Out player, and the gain shown equal to re-planning the week; manual nights kept as set, reported and never refilled, held fixed under matchup weighting and in the free-agent search; each player's nights, next week and heat; then the routes on real data including every 400, rank and form on every player, and malformed manual nights dropped, and the free agent pool route |
| `test_goalie_planning.py` | goalie planning with no database: minutes recovered exactly from GA and GAA and never assumed to be an hour, the measured minutes curve, a pull as a variant outside the distribution and its lopsided cost to GAA, and limits that move with the opponent |
| `test_accounts.py` | the temporary accounts, on real routes under `zztest*` usernames: one account can never read, save over, open, delete or even version-check another's league; a stale save is refused as a conflict; the page carries its league escaped so a team name cannot close the script tag; lockout, sign-up limits (a spoofed forwarding header does not reset them), size and league caps; an account deleted by `manage_accounts.py` signed out where it was signed in; and the key lists in Python and JS not drifting. **It deletes every `zztest*` account** — do not use that prefix by hand |
| `test_player_form.py` | form for the roster view: trends are a standard-error test (a streak inside a noisy player's spread is flat, too few games is no trend), PP share never reaches past the recent games, venue needs games at both, goalies judged on starts |
| `test_yahoo_matchup.py` | the score scrape with no network: URLs carry week and mid1, a dash is not a zero, starred columns are unscored, SV% maps to SVpct, each wrong page named, and the routes (test mode, columns, private, bad input, bookmarklet parse) |
| `test_bench_points.py` | bench points with no network: only BN counts (never IR), a bench player replaces only a starter whose slot he fits and a goalie only a goalie, an idle starter is offered and marked, a swap names what it wins, ties and costs with the exact record before and after, GAA and SV% rebuilt from their parts with minutes recovered from GAA, points leagues get totals but no swaps; the API reader's compact day shape and its private refusal; the routes; and the private path — game rows as Yahoo stats (PPA from PPP and PPG, a goalie's GAA from his own seconds), unsupported categories reported, two Elias Petterssons told apart by the page's positions, week totals from starters only, and bench_points reading the built shapes exactly as it reads the API's |
| `test_yahoo_transactions.py` | the transactions scrape with no network: API stand-ins and synthetic pages describing the same league must come out identical; waiver claims vs free-agent pickups, drops to waivers vs free agents, a trade's two rows as one trade with picks, the year turning at New Year, page times in the sent zone, commissioner settings changes kept out, API paging and every error, and the routes |
| `test_yahoo_rosters.py` | the roster scrape with no network: League IDs, parsing (IR/IR+/NA out, empty slots, Yahoo's status badge — NA in a starting slot is out, DTD is not), private / not-found / unrelated pages each named, two Elias Petterssons told apart by Yahoo's player record, the pipeline copies not drifting, and the routes in and out of test mode |
| `test_adp.py` | the ADP scrape: reading Yahoo's numbers (a dash is an absence, not a zero), stopping paging at the first undrafted player, and the crosswalk — including the two Elias Petterssons Vancouver actually carries. Stubs the API; needs no database |
| `test_aging.py` | the age curve: that it is a re-basing rather than a haircut (old down, young up, peak untouched), that decline accelerates, that peripherals outlast scoring, that `plusMinus` is never scaled, and the 1-February birthday arithmetic. Pure maths — the only suite needing no database |

Adding a suite means adding its filename to `TESTS` in `run_all.py`.

## Conventions

- Every file starts with a docstring: purpose, `Author - Jason Druckenmiller`,
  `Created` / `Updated` dates. Keep this style on new files and bump `Updated` on edits.
- API routes return `{"status": "success"|"error", ...}` JSON and catch exceptions into
  a 500 with `{"status": "error", "message": str(e)}`.
- Postgres columns are camelCase and quoted in raw SQL (`"positionCode"`, `"final_projections"`).
- Blueprints only; add new areas as a blueprint in `routes/` and register it in `app.py`.
- **Every page works on a phone.** Build and check it at 375px as well as desktop,
  following *Phone layout* below. A page that only works on a desktop is not finished.

## Phone layout (every page)

Every page has to work at 375px wide as well as on a desktop — drafts and lineups
get set from phones. The home page, draft prep, Schedules, League Database and
the terms page all follow this, and a new page follows it from its first commit
rather than getting a phone pass later. The breakpoint is Tailwind's `sm`
(640px): below it is "a phone".

**Check it.** Load the page in the browser pane at the mobile preset (375×812)
and at desktop width. `document.documentElement.scrollWidth` must equal
`innerWidth` — the page itself never scrolls sideways; only a table or a strip of
tabs inside it may.

**The pattern, and the shared pieces that implement it:**

- **Header:** `flex flex-wrap justify-between items-center gap-x-4 gap-y-2`, title
  `text-xl sm:text-2xl`, then `partials/page-nav.html`. The nav scrolls sideways
  on one line, so the header has to wrap to give it a row of its own.
- **Spacing:** `main` at `p-3 sm:p-4 md:p-6` with `gap-4 sm:gap-6`; cards at
  `p-3 sm:p-4`. Desktop padding spends a phone's width on nothing.
- **Tables** sit in an `overflow-x-auto` wrapper (or `.table-scroll` when the
  header should stick) and carry `.data-table`, which tightens cell padding and
  enlarges checkboxes on phones. A table still wider than the screen freezes its
  identifying column with `.col-sticky`. That cell is opaque, so a tinted row has
  to paint its tint on it too — see the `tr[data-tag]` rules.
- **Rows that would wrap to several lines** — tabs, chips, filters — become one
  sideways-scrolling row (`overflow-x-auto whitespace-nowrap`, items `shrink-0`),
  or fold behind a button on phones, as draft prep's Columns does.
- **Scroll boxes inside the page** are capped in viewport units on phones
  (`max-h-[70dvh] sm:max-h-[42rem]`), so the page can still be scrolled past
  them. `dvh`, not `vh`: `vh` counts the toolbar a phone browser slides away.
- **Modals holding a form go full screen below `sm`:** wrapper `p-0 sm:p-4`,
  dialog `h-full sm:h-auto sm:max-h-[90vh]`, and the footer outside the scrolling
  body so its buttons stay on screen. A short message modal only needs
  `max-h-[90vh] overflow-y-auto`.
- **Touch:** nothing needed only on hover or in a `title`; tap targets at least
  `py-2` on phones; say "select" or "tap", not "click". Put an action near what
  it acts on — draft prep repeats its tag buttons in a bottom bar because up top
  they are a screen away from the rows.
- **A stacked master/detail** (a list above what it opens) scrolls the detail
  into view on a pick, or the tap looks like it did nothing — see League
  Database's teams and rosters.
- **Inputs** use `.form-input` / `.form-select`. `styles.css` sets them to 16px on
  phones, because iOS Safari zooms into any smaller focused field and stays zoomed.

**Two Tailwind traps.** Toggle visibility with `sm:hidden` / `max-sm:hidden`,
not `hidden sm:block` — `styles.css` restates `.hidden`, and which wins then
depends on stylesheet order. And never add a utility from script that appears
nowhere in the markup: the vendored CDN build generates it only once it notices
it, so it is missing when first needed (a `pb-28` set on the first tick measured
0px). Put that rule in `styles.css` instead, as `body.has-selection` is.

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
work without one (and Standalone mode, below, needs no league at all):

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
- `/` and `/league/` consume the session; draft-prep, `/schedules/` and `/standalone/` are
  deliberately league-agnostic.
- `users.tos_accepted_version` is an INTEGER (`Config.TOS_VERSION`), while
  `index.html` keeps its own `CURRENT_TERMS_VERSION` date string in
  localStorage. Two representations of one thing; reconcile when the terms next
  change.
- Token refresh has no lock: two concurrent requests on an expired token both
  refresh, and the later write wins. Harmless now; revisit with the Phase 2 worker.
- Automated transactions are stubs. Standalone mode plans add/drops but executes nothing.
- **The bookmarklet has never run end to end by a real click** — see *Scraping
  rosters from Yahoo*. Every private-league feature rests on it.
- **Bench points for private leagues reads ~180 pages on a first catch-up**
  (about a minute) and keeps the lineups per device; a second device reads them
  again. Points leagues get bench totals but no swaps.
- **Season to date is closed until 2026-10-14**, by design (*Stat Sourcing*).
  *Combined* — a blend of season rate and projection — is not built; the
  principled version shrinks each stat toward the projection by how fast it
  stabilises, which wants measuring on a past season first.
- **A player with games but no projection** (an unprojected call-up) is not in
  `final_projections`, so no source can value him or match him on a roster.
- **Line numbers differ from Daily Faceoff's** for about a third of forward
  lines and half the pairs, though the groups themselves agree ~90% of the
  time — see *Lines from shift charts*. Their order is partly editorial.
- The accounts are temporary and store one copy of a league per member —
  retire them with the Yahoo sync (*When Yahoo API access is granted*). The
  Yahoo `/logout` does `session.clear()`, which signs out of an account too;
  harmless while the Yahoo login is unused.
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
