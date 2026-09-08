# Deploying draft-prep to Render

Gets the draft-prep page online: a Render Postgres holding the projection
tables, a Flask web service serving the app, and the preseason pipeline run
from your machine to fill the database.

`render.yaml` is a Blueprint — Render reads it and creates both services.

## 1. One-time setup

- A Render account with a payment method (both services below are paid tier).
- This repo connected to Render (GitHub authorization).

## 2. Deploy the Blueprint

1. Render Dashboard → **New** → **Blueprint**.
2. Pick `Interestingkiwi/fantasy-streams-2`, branch `main`.
3. Render shows what `render.yaml` will create:
   - **`fantasy-streams-db`** — Postgres, `basic-256mb`, Virginia.
   - **`fantasy-streams`** — Python web service, `starter`, Virginia, running
     `gunicorn app:app`.
   `DATABASE_URL` is wired from the database; `FLASK_SECRET_KEY` is generated;
   `APP_ENV=production` is set. `SKIP_SCHEMA_INIT` is deliberately **not** set:
   it used to be `1` because draft-prep needs no admin tables, but Yahoo auth
   stores tokens in `users`, so the startup DDL has to run. It is all
   `CREATE TABLE IF NOT EXISTS`.
4. **Apply**. First build takes a few minutes (installing pandas/numpy).

If Render rejects the database `plan` value, check the current slug at
<https://render.com/docs/postgresql-plans> and update `render.yaml`.

### Python version

`render.yaml` pins `PYTHON_VERSION=3.13` (local dev is on 3.14, which Render
may not offer yet). If the build fails on a `numpy` or `pandas` wheel, drop it
to `3.12`.

## 3. Populate the database

The web service will deploy green, but the draft-prep API returns errors until
the projection tables exist — the pipeline creates them
(`to_sql(..., if_exists="replace")`).

1. In the Render dashboard, open **`fantasy-streams-db`** and copy the
   **External Database URL** (not the Internal one). Append `?sslmode=require`
   if it isn't already there.

2. Run the pipeline locally against that URL — it does **not** touch your `.env`
   when `DATABASE_URL` is set on the command line:

   **PowerShell**
   ```powershell
   $env:DATABASE_URL = "postgresql://...@dpg-...virginia-postgres.render.com/fantasy_streams?sslmode=require"
   cd preseason_db_build
   ..\venv\Scripts\python.exe build_database.py
   cd ..
   Remove-Item Env:\DATABASE_URL
   ```

   **bash**
   ```bash
   cd preseason_db_build
   DATABASE_URL="postgresql://...@dpg-...virginia-postgres.render.com/fantasy_streams?sslmode=require" \
     ../venv/Scripts/python.exe build_database.py
   cd ..
   ```

   Each script prints its target on startup — `DB engine -> dpg-... / fantasy_streams`
   confirms you're writing to Render, not local. Full run is ~4 minutes.

## 4. Verify

- `https://fantasy-streams.onrender.com/` → landing page.
- `https://fantasy-streams.onrender.com/draft-prep/` → draft-prep loads.
- `.../draft-prep/api/projections` → `{"status": "success", "data": [ ...928 rows... ]}`.

## 5. Refreshing projections

Re-run step 3 whenever you want fresh numbers (new completed games, roster
moves, injury changes). Later this can become a scheduled Render job; for now
it's a manual local run.

## Phase 1 note

When Yahoo auth lands, drop `SKIP_SCHEMA_INIT` so the admin/league tables get
created, and add `YAHOO_CONSUMER_KEY` / `YAHOO_CONSUMER_SECRET` to the web
service.
