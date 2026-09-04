# Migration plan: `fantasy-streams` → `fantasy-streams-2`

Bringing the pages from the old repo (`Interestingkiwi/fantasy-streams`) into this
one. The old app works but is a single ~5,300-line `app.py` plus a
1,400-line `db_builder.py`; this repo is the clean rebuild (Flask blueprints,
SQLAlchemy Core, Postgres).

## Where each repo stands

| | Old (`fantasy-streams`) | New (this repo) |
|---|---|---|
| Structure | monolithic `app.py` + `db_builder.py` + `jobs/` | blueprints in `routes/`, SQLAlchemy Core |
| Auth | full Yahoo OAuth2 (`yfpy` + `yahoo_fantasy_api` + `yahoo_oauth` + `requests_oauthlib`) | `/login` stubbed; login modal UI already built in `templates/index.html` |
| DB | Postgres: **admin schema** (`users`, `league_updaters`, `job_logs`, `scheduled_transactions`) + **per-league schema** (~16 tables: `league_info`, `teams`, `scoring`, `lineup_settings`, `weeks`, `matchups`, `rosters`, `free_agents`, `waiver_players`, `rostered_players`, `transactions`, `daily_player_stats`, `daily_bench_stats`, `daily_lineups_dump`, `rosters_tall`, `db_metadata`) | projection tables only (`final_projections`, `historic_*`, `player_directory`, …) |
| Background | Redis + RQ worker (`worker.py`), gevent/gunicorn, APScheduler | none |
| Premium | `requires_premium` gating via `users.is_premium` | none |
| Pages | matchup, lineups, season-history, trade-helper, schedules, free-agents, goalie-planning, league-database, settings, streaming, scheduled add/drops | draft-prep only |

## Locked decisions

### 1. One DB idiom: SQLAlchemy Core everywhere

Raw SQL lives inside `text()` with `:name` bind params. Shared `engine` from a
single module. Transactions via `with engine.begin():`. Pool settings include
`pool_pre_ping=True` (replaces the hand-rolled liveness checks in the old
`database.py`).

- Ported queries need `%s` → `:name` conversion and `RealDictCursor` →
  `row._mapping`. Do it per-feature as each page is ported, not big-bang; add a
  smoke test per route since there is no existing suite.
- Bulk-insert hot paths in league sync may drop to the raw DBAPI cursor
  (`conn.connection.cursor()` + `psycopg2.extras.execute_values`) — the driver
  under SQLAlchemy is still psycopg2, so the escape hatch is always available.

### 2. League sync runs on Redis + RQ, with an inline fallback

One enqueue site:

```python
if REDIS_URL:
    queue.enqueue(run_task, ...)
else:
    run_task_inline_in_thread(...)   # local dev without Redis
```

- Prod: Redis instance + always-on worker process (`worker.py`). On Render that
  is a paid worker service + Redis.
- Local dev: no Redis needed for day-to-day work; run `redis-server` + the
  worker only when testing sync itself.
- RQ is kept (not replaced with a bare thread) because it already solves
  durability, retries, and cross-process job dedup, and Phase 4 (scheduled
  transactions firing at wall-clock times) needs a real worker regardless.
- The old `trigger_smart_update` freshness logic (skip if <15 min old;
  roster-only vs full depending on whether a full ran today) ports directly onto
  the enqueue path.

### 3. Slim-ish Yahoo auth

- `requests_oauthlib` for the OAuth2 authorization-code flow (as in the old
  `/login` + `/callback`).
- **Drop `yahoo_oauth` now** — hand-roll token refresh (~15-line POST to the
  token URL). Removes the file-based token handling and the
  tempfile-per-request dance, both flagged "NOT thread-safe" in the old code.
- **Keep `yfpy` for reads during the port** so Yahoo's deeply-nested JSON
  parsers don't have to be rewritten under pressure. Revisit shedding it once
  pages are stable.
- **Add `yahoo_fantasy_api` (yfa) only in Phase 4** for add/drop writes.

Constraints regardless:

- Redirect URI must exactly match the Yahoo Developer console registration.
  Local dev needs an https tunnel or a registered `http://localhost` callback.
- Register the app for **read/write** scope now — scope is fixed at
  registration and Phase 4 needs writes.
- Access tokens expire in 1 hour; refresh handling is mandatory.

## Phased plan

### Phase 0 — Foundations (no user-facing pages)

1. Move the hardcoded Postgres credentials in `export_postgres.py` and
   `preseason_db_build/db_config.py` fully to env vars; scrub from history if
   feasible. *(Previously agreed as the first task.)*
2. Shared DB access module usable outside `preseason_db_build/` — SQLAlchemy
   `engine` with `pool_pre_ping`, `text()` helpers, `row._mapping` conventions.
3. Schema-init module: port the admin tables + `db_builder._create_tables` into
   a versioned `schema/` that runs on startup.
4. Config from env: `FLASK_SECRET_KEY`, `YAHOO_CONSUMER_KEY` / `_SECRET`,
   `REDIS_URL`.
5. Install `rq` + `redis`; add the enqueue-or-inline helper.

### Phase 1 — Yahoo OAuth + session (gates everything league-specific)

- `routes/auth_routes.py`: real `/login` (build Yahoo auth URL), **new
  `/callback`** (code → token exchange → fetch `guid` + user's NHL leagues),
  `/logout`, `/api/my_leagues`, `/api/switch_league`.
- `users` table + `save_user_credentials` + hand-rolled token refresh.
- Keep the dev-backdoor login (`DEV_BACKDOOR_PASS`) — it is how to test without
  Yahoo.
- Port `requires_premium` as a pass-through flag for now; real logic later.
- Port the `home.html` shell + nav + league switcher so ported pages have
  somewhere to live.
- `settings` page alongside this phase.

### Phase 2 — League ETL (everything downstream reads this)

- Port `db_builder.py` → a `league_sync/` package: pulls a league's settings,
  teams, weeks, matchups, rosters, transactions, and daily stats from Yahoo
  into the per-league tables.
- **Sequencing (D1 × D2):** port `db_builder` to RQ first with its psycopg2 SQL
  intact and get sync working end-to-end; convert its queries to `text()` as a
  separate, testable step afterward.
- Endpoints: `/api/db_status`, `/api/update_db`, `/api/db_timestamp`, and the
  existing `/api/db_log_stream` SSE progress stream backed by `job_logs`.

### Phase 3 — Analysis pages, in dependency order

1. **League Database viewer** — displays what Phase 2 ingested; simplest
   end-to-end proof.
2. **Schedules** — NHL schedule + off-days; mostly self-contained.
3. **Matchup Dashboard** — core feature; needs scoring settings + rosters +
   projections + weeks. Port the `players ⋈ final_projections` join
   (`build_player_query`) here.
4. **Lineups / roster optimizer** — `get_optimal_lineup` + `lineup_settings`;
   heavy logic.
5. **Free Agent Finder** — `free_agents` / `waiver_players` + projections +
   schedule density.
6. **Goalie Planner** — schedule + goalie projections + probable starters.
7. **Season History** — largest JS in the old repo (~1,420 lines); needs the
   full daily-stats history ETL.
8. **Trade Helper** — category-strength math across rosters; builds on the
   matchup infrastructure.

### Phase 4 — Automation (last; only write path to Yahoo)

- **Scheduled Add/Drops** — `scheduled_transactions` table + a scheduler
  executing at set times + Yahoo write scope + `yfa` add/drop calls. Highest
  risk (mutates the user's real team); port once everything else is stable.

### Cross-cutting

- Standalone mode (already stubbed) is a parallel track — several old pages
  already support manual entry of lineup/league settings.
