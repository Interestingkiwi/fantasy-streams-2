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
| `app.py` | Entry point; registers the `main`, `auth`, `draft` blueprints; `python app.py` -> debug server on :5000 |
| `routes/main_routes.py` | `/` (renders signed-in or signed-out from the session), `/terms`; `/standalone` still a placeholder |
| `routes/auth_routes.py` | Yahoo OAuth: `/login`, `/callback`, `/logout`, `/api/session`, `/api/my_leagues`, `/api/switch_league` |
| `yahoo_auth.py` | Yahoo OAuth2 client — consent URL, code exchange, token refresh, authenticated API calls (see *Auth* below) |
| `config.py` | `Config` from env + `check_config()` fail-fast at startup |
| `schema.py` | Idempotent DDL for the admin + per-league tables; runs at startup (`SKIP_SCHEMA_INIT=1` to bypass) |
| `jobs.py` / `worker.py` | `enqueue()` — RQ when `REDIS_URL` is set, background thread when it isn't |
| `routes/draft_routes.py` | `/draft-prep/` page + JSON APIs: `/api/available-stats`, `/api/projections`, `/api/rank-players` (POST), `/api/playoff-schedule` (POST) |
| `db.py` | **Canonical** SQLAlchemy Core engine + query helpers for the web app (`from db import engine, text`) |
| `ranking_utils.py` | Ranking engine — see *Ranking* below |
| `preseason_db_build/` | Offline pipeline that builds the `final_projections` table (see below) |
| `preseason_db_build/db_config.py` | Pipeline-only SQLAlchemy `engine` from `DATABASE_URL` |
| `preseason_db_build/season_config.py` | `season_game_count()` — season length read from `nhl_schedule` (84 from 2026-27) |
| `templates/index.html` | Landing/login page |
| `templates/pages/draft-prep.html` | ~1,375-line draft prep UI: League Settings modal, projection table, ranking controls |
| `static/styles.css`, `static/tailwind.js` | Styles + vendored Tailwind |

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

**No `scope` is sent** — Yahoo fixes permissions at app registration, so the
app must be registered **read/write** (Phase 4 writes rosters). A **403 from
the Fantasy API almost always means that permission is missing**, not that the
token is bad; `_forbidden_hint()` says so in the error rather than leaving a
bare status code.

**Parsing Yahoo JSON:** entities come back as lists of partial dicts under
numeric string keys. `_iter_leagues()` walks for the key it wants and merges,
rather than indexing by position, which is what made the old parsers brittle.

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

## Draft prep page

Everything except the ranking-method control lives in the **League Settings** modal:
league structure (categories/points + number of teams), roster settings, playoff weeks,
and the skater/goalie category grids. **Rank Via** (Roster Setting / Projection Only /
Balanced) sits on the page itself, in the filter bar, and re-ranks on click.

State is `localStorage`, all keys prefixed `fs_`: `fs_selectedStats`, `fs_statWeights`,
`fs_leagueMode`, `fs_pimPolarity`, `fs_numTeams`, `fs_rosterMode`, `fs_rosterSlots`,
`fs_playoffWeeks`, `fs_rankMode` (plus `fantasy_streams_tags` for player tags).

On load the page ranks against those saved settings by *replacing* the
`/api/projections` call with `/api/rank-players`, never adding to it — the ranking maths
costs ~40ms against a ~2s payload, so a returning user waits no longer. With no
categories selected there is nothing to rank, and the plain call stands.

**Playoff weeks** are display-only and never touch the rankings — they drive a `Playoff`
column showing games in the selected weeks, colour-coded against the league average,
with light-night games (dates under `LIGHT_NIGHT_MAX_GAMES`) as a suffix. Week numbers
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
python app.py            # dev server, http://localhost:5000, debug=True

# rebuild projections (long-running, hits external APIs):
cd preseason_db_build && python build_database.py
```

- Requires `.env` with `DATABASE_URL` (local Postgres). `.env` is git-ignored.
- No test suite exists yet.
- `requirements.txt` is plain UTF-8. (It used to be UTF-16; if an editor shows CJK
  gibberish, that is a stale copy.)

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

Phases 0 and 1 are done: config/schema/jobs foundations, and Yahoo OAuth end to
end (see *Auth* above). **Phase 2 — the league ETL (`db_builder.py` ->
`league_sync/`) — is next**; it is what turns a selected league into data.

Already pulled forward out of order: the NHL schedule (MIGRATION Phase 3 item 2) is
built as `nhl_schedule` by `scrape_nhl_schedule.py`, because playoff-week comparison
needed it. Note the old repo's version in `jobs/create_projection_db.py` hardcodes
`START_DATE` / `END_DATE` to 2025-26 — don't port it, the new one reads the season
from the API. When porting anything else from that repo, expect raw psycopg2 with `%s`
placeholders; this repo is SQLAlchemy Core with `text()` and `:name` binds.

## Known issues / cleanup backlog

- Yahoo OAuth works, but **no page consumes the session yet** — `/` shows the
  active league and draft-prep is still league-agnostic. League ETL is Phase 2.
- `users.tos_accepted_version` is an INTEGER (`Config.TOS_VERSION`), while
  `index.html` keeps its own `CURRENT_TERMS_VERSION` date string in
  localStorage. Two representations of one thing; reconcile when the terms next
  change.
- Token refresh has no lock: two concurrent requests on an expired token both
  refresh, and the later write wins. Harmless now; revisit with the Phase 2 worker.
- The automation / streaming features are stubs.
- `/api/projections` and `/api/rank-players` each take **~2s**, essentially all of it
  `SELECT *` plus jsonify of 864 rows x ~37 columns. The ranking maths is ~40ms of that.
  Worth narrowing the column list or paginating server-side.
- Projected goalie games sum to ~2,974 against a league total of 2,688 (32 x 84), because
  every goalie is projected independently and a starter and his backup can't both hit
  their assigned workload. Long-standing, and unchanged in proportion by the 84-game move.
- `position_eligibility.csv` is a manual export and has to be refreshed each preseason;
  the eventual source is the Yahoo API once OAuth lands.
