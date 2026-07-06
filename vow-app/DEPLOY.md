# Deploying Vow to Render

Vow is a long-running Flask app that writes JSON files and runs background
threads, so it needs a real server (not serverless). Render fits as-is.

## Before you deploy

- Your `OPENAI_API_KEY` is in `vow-app/.env`, which is gitignored — it will **not**
  be pushed. You'll set it as a secret in Render instead.
- The repo already contains everything Render needs: `requirements.txt` (incl.
  `gunicorn`), the start command, and `render.yaml`.

## Option A — Blueprint (uses render.yaml, easiest)

1. Push the repo to GitHub (see the git commands your assistant gives you).
2. In Render: **New ▸ Blueprint**, pick this repo. Render reads `render.yaml`
   and configures the `vow` web service automatically.
3. When prompted, paste your **OPENAI_API_KEY**.
4. Click **Apply**. Render builds and gives you a public URL like
   `https://vow.onrender.com`.

## Option B — Manual (dashboard, no blueprint)

1. **New ▸ Web Service**, connect the repo.
2. Settings:
   - **Root Directory:** `vow-app`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT server:app`
3. **Environment ▸ Add** `OPENAI_API_KEY` = your key.
4. **Create Web Service.**

> Keep it **1 worker** (`-w 1`). The live "analyzing…" progress uses an
> in-memory job list; multiple workers wouldn't share it.

## Data persistence

By default the app ships with the sample wedding baked in as local JSON files,
and any edits made on the live site are lost when the instance restarts or
redeploys — perfectly fine for a demo.

### Option A — Supabase (recommended)

All wedding data lives as JSON documents behind one storage layer
(`storage.py`); pointing it at Supabase gives you a real Postgres database
that survives restarts, redeploys and multiple instances.

1. Create a project at supabase.com, open the **SQL editor**, and run
   `supabase_schema.sql` (creates the `vow_documents` table with RLS on and
   no public policies — only the server can touch it).
2. From **Project Settings ▸ API** copy the URL and the `service_role` key,
   and set them as env vars (or in `vow-app/.env` locally):
   - `SUPABASE_URL` = `https://<project>.supabase.co`
   - `SUPABASE_SERVICE_KEY` = the service_role key (server-side only — never
     ship it to a browser).
3. Push the current data up once: `python migrate_to_supabase.py`
4. Restart the app. It now reads and writes Supabase; the local `data/`
   files are ignored (kept as a seed/fallback). Setting
   `VOW_STORAGE_BACKEND=files` forces the old behavior at any time.

### Option B — Render persistent disk (files, single instance)

1. In `render.yaml`, uncomment the `disk:` block (mounts at `/var/vow-data`).
2. Add an env var **`VOW_DATA_DIR`** = `/var/vow-data`.
3. Both the web app and the agent honor it (see `storage.py`).
4. The disk starts empty, so copy your starting `data/*.json` into it once.

## Notes

- First request after the app sleeps (free tier) can be slow to wake — normal.
- Logs (token cost per call) are written to `logs/run_log.jsonl` inside the
  instance; they reset with the instance unless you also persist that folder.
