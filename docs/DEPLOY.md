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
   `DATABASE_URL` on the web service is wired to the database automatically.
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

## When Phase 0 (PR #3) merges

`app.py` will then load `config.py` and run schema init. Uncomment the three
env vars at the bottom of `render.yaml`'s web service and redeploy:

- `APP_ENV=production` — debug off, secure cookies, https URL scheme.
- `FLASK_SECRET_KEY` (`generateValue: true`) — required in production.
- `SKIP_SCHEMA_INIT=1` — draft-prep doesn't use the admin/league tables.
