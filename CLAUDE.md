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
- **Planned but not yet wired:** Redis + RQ workers, Gevent/Gunicorn, Yahoo OAuth

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Entry point; registers the `main`, `auth`, `draft` blueprints; `python app.py` -> debug server on :5000 |
| `routes/main_routes.py` | `/`, `/standalone`, `/terms` — mostly placeholders |
| `routes/auth_routes.py` | `/login` — **stubbed** Yahoo OAuth (returns a fake `auth_url`) |
| `routes/draft_routes.py` | `/draft-prep/` page + JSON APIs: `/api/available-stats`, `/api/projections`, `/api/rank-players` (POST) |
| `ranking_utils.py` | Ranking engine. Points leagues -> fantasy points/game. Category leagues -> asymmetric capped z-scores vs a 40+ GP baseline, with a goalie category multiplier to offset positional scarcity. |
| `preseason_db_build/` | Offline pipeline that builds the `final_projections` table (see below) |
| `preseason_db_build/db_config.py` | Shared SQLAlchemy `engine` from `DATABASE_URL` |
| `templates/index.html` | Landing/login page |
| `templates/pages/draft-prep.html` | ~840-line draft prep UI: stat toggles, interactive projection table, trends |
| `static/styles.css`, `static/tailwind.js` | Styles + vendored Tailwind |

## The projection pipeline (`preseason_db_build/`)

`build_database.py` runs the steps in order via `subprocess`. Roughly:

1. `add_tables.py` — schema/logs
2. `historic_data_skaters.py` / `historic_data_goalies.py` — NHL API season stats
3. `append_advanced_skaters.py` / `append_advanced_goalies.py` — MoneyPuck advanced stats
4. `create_player_directory.py`
5. `scrape_ep_rookies.py` (EliteProspects) / `enrich_ahl_stats.py` (HockeyTech feed)
6. `scrape_injuries.py` (ESPN injuries API)
7. `calculate_skater_projections.py` / `calculate_goalie_projections.py` —
   60/30/10 time-decay weighting of the last 3 seasons (per-game), paced to 82 games,
   with production & peripheral trend labels; 40-game 3-yr minimum, 10-game per-season minimum
8. `apply_injury_adjustments.py` — final downward adjustments -> `final_projections`
9. `sync_current_rosters.py` — overwrites `teamAbbrevs` in `final_projections` and
   `player_directory` with each player's current NHL team (offseason trades / signings);
   pulls all 32 rosters from `api-web.nhle.com`. Players not on any current roster keep
   their last-season team string.

External data sources: `api.nhle.com`, `api-web.nhle.com`, `moneypuck.com`,
`eliteprospects.com`, `lscluster.hockeytech.com`, `site.api.espn.com`.

**Import-path quirk:** pipeline scripts import `from db_config import engine`
(no package prefix), so they must be run with the working directory set to
`preseason_db_build/`. The Flask app imports `from preseason_db_build.db_config import engine`
and runs from the repo root.

## Running

```bash
# activate the existing venv, then:
python app.py            # dev server, http://localhost:5000, debug=True

# rebuild projections (long-running, hits external APIs):
cd preseason_db_build && python build_database.py
```

- Requires `.env` with `DATABASE_URL` (local Postgres). `.env` is git-ignored.
- No test suite exists yet.
- `requirements.txt` is UTF-16 encoded — it looks garbled in editors but pip reads it fine.

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
and Yahoo-auth decisions already settled. Phase 0 starts with the Postgres-credentials
task below.

## Known issues / cleanup backlog

- **Hardcoded production Postgres credentials** in `export_postgres.py` and (via a default)
  around `preseason_db_build/db_config.py`. Agreed first task next session: move the
  connection string fully to env vars and scrub it from history if feasible.
- Yahoo OAuth (`/login`) and the automation/streaming features are stubs.
- Some player names carry mojibake (`Gustav Lindstr�m`) from an upstream UTF-8/latin-1
  decode bug in the NHL/EliteProspects data handling.
- `final_projections` still includes ~190 players not on any current NHL roster
  (retired / UFA / minors) with full 82-game projections. `sync_current_rosters.py`'s
  roster map is the signal to filter or flag them.
