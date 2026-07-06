# Deploying behind a single origin

> **Going to a public VPS with HTTPS + backups?** This page covers the app's
> single-origin model and the dev Docker Compose stack. For the production setup
> — Caddy/TLS, Litestream backups, firewall, and a full VPS runbook — see
> [deploy-production.md](deploy-production.md).

In production the FastAPI app serves **both** the JSON API (under `/api/*`) and
the built React SPA (everything else) from one origin. There is no separate
frontend server and no CORS — the browser sees a single host. The ingestion
worker runs alongside the API as a second process sharing the same SQLite file.

> Authentication is **Steam OpenID**, opt-in via `DEADLOCK_BASE_URL`. Set it to the
> app's public origin to require login (and CSRF) on every write.
>
> **Writes fail closed.** If you don't set `DEADLOCK_BASE_URL`, writes return `403`
> until you explicitly set `DEADLOCK_OPEN_WRITES=1` — the private single-user escape
> hatch that runs every write as one local user with no login. This means a deploy
> that forgets to configure auth rejects writes instead of silently exposing a
> shared admin panel. Set exactly one of the two; reads are always open.

## Environment variables

| Variable            | Used by | Required | Meaning |
|---------------------|---------|----------|---------|
| `DEADLOCK_DB`       | API     | No (image defaults to `/data/tracker.db`) | Path to the SQLite database the API reads. The worker uses its own `--db` flag instead (see below). |
| `DEADLOCK_BASE_URL` | API     | No (unset → auth off) | The app's public origin (e.g. `https://stats.example.com`). When set, Steam login is required: writes go through `require_user` (401 without a session) and are CSRF-protected. Steam redirects back to `<base>/api/auth/callback`. Use `https://` in production so the login cookies are `Secure`. |
| `DEADLOCK_OPEN_WRITES` | API  | No (unset → writes closed unless `DEADLOCK_BASE_URL` is set) | Set to `1` for a private single-user deploy: writes are open with **no login**, running as one local user. Ignored when `DEADLOCK_BASE_URL` is set (Steam login wins). With **neither** variable set, writes fail closed — every write returns `403` while reads stay open — so a deploy that forgets to configure auth can't expose a shared admin panel. |
| `STEAM_API_KEY`     | Worker  | No (clean no-op when unset) | Steam Web API key for persona (display-name) enrichment during worker maintenance. Without it, accounts fall back to bare ids. (Not needed for login — Steam OpenID doesn't use it.) |
| `DEMO_ACCOUNT_ID`   | API     | No (unset → no demo button) | Account id highlighted as a live demo to signed-out visitors. When set, the empty landing shows a "View a live demo profile" button linking to `/player/<id>` (a public, read-only profile), and link previews for `/player/<id>` pages are personalized. Any tracked account (or any account we hold match data for) works. |

All are read fresh from the environment at runtime (`api/config.py`), so a
deploy sets them without any code change.

### Public profiles & link previews

Reads are open, so any account the app holds data for is viewable at
`/player/<account_id>` — a read-only view with every management and write control
hidden. This is the link you share; `DEMO_ACCOUNT_ID` just points the landing
button at one such profile.

The API injects Open Graph / Twitter card tags into the SPA shell per request
(`api/meta.py`), so a pasted link unfurls with a real title instead of a generic
one. A `/player/<id>` link is personalized with the account's public display name
(Steam persona or bare id — never anyone's private label) and current rank;
everything else gets generic site tags, as does any lookup that misses or errors.
`DEADLOCK_BASE_URL` supplies the `og:url`; when it's unset the tag is omitted
rather than guessed.

## Run with Docker Compose (recommended)

The image is multi-stage: a Node stage builds `frontend/dist`, then a Python
stage installs `requirements.txt` and serves the API + built SPA with uvicorn.
[docker-compose.yml](../docker-compose.yml) defines two services — `api` and
`worker` — sharing one named volume (`deadlock-data`) for the database.

1. Create a `.env` next to `docker-compose.yml` for config/secrets:

   ```dotenv
   DEADLOCK_BASE_URL=https://stats.example.com   # set to require Steam login (public deploy)
   # DEADLOCK_OPEN_WRITES=1                       # instead, for a private single-user deploy (no login)
   STEAM_API_KEY=your-steam-web-api-key          # optional
   ```

   Set exactly one of `DEADLOCK_BASE_URL` or `DEADLOCK_OPEN_WRITES`. With neither,
   writes fail closed (`403`) while reads keep working.

2. Build and start both services:

   ```sh
   docker compose up --build
   ```

The app is now at `http://localhost:8000` — the SPA at `/`, the API at `/api/*`.

### Database migration & persistence

The container entrypoint runs `python -m tracker.migrate "$DEADLOCK_DB"` on every
boot before starting its process. Migration is idempotent, so:

- the **API** container creates and migrates the DB on first boot (it works
  standalone, even before the worker runs);
- the **worker** also migrates on startup (a harmless no-op re-run).

On first boot you'll see `Migrated to schema version N ...` in the logs; on later
boots, `Already at schema version N, nothing to do.` The DB lives on the
`deadlock-data` volume, so it survives `docker compose down` / `up`.

### Reclaiming disk after archive housekeeping (one-time)

Match-metadata bodies (1.2–1.6 MB each) used to be stored twice — in
`matches.raw_json` and in `raw_api_responses`. Two one-shot worker commands
converge an existing database onto the deduplicated + compressed layout:

```sh
# 1. Compress any legacy uncompressed raw_json rows (batched, resumable).
docker compose run --rm worker python -m ingest --db /data/tracker.db compress-raw-json
# 2. Delete the now-redundant duplicate metadata bodies from raw_api_responses.
docker compose run --rm worker python -m ingest --db /data/tracker.db prune-archive
```

Both are idempotent and safe to run on a live stack (they batch their writes so
the API's writer lock never starves). **But deleting rows does not shrink the
SQLite file** — freed pages are only reused internally. To actually return the
disk to the OS you must run an **offline `VACUUM`**, which rewrites the whole
file and therefore needs *exclusive* access (stop every writer first) and up to
**~2× the database size** in free space temporarily:

```sh
# Docker: stop both services, VACUUM via a one-off container, restart.
docker compose stop
docker compose run --rm worker python -c "import sqlite3; sqlite3.connect('/data/tracker.db').execute('VACUUM')"
docker compose up -d
```

Bare metal — stop the API (uvicorn) and the worker daemon, then:

```sh
python -c "import sqlite3; sqlite3.connect('data/tracker.db').execute('VACUUM')"
```

After the initial cleanup these commands are a permanent no-op: the worker no
longer double-writes metadata, so there is nothing left to prune, and new
`raw_json` rows are written compressed from the start.

### Running the worker alongside the API

The `worker` service runs the ingestion daemon continuously:

```
python -m ingest --db /data/tracker.db run-daemon
```

It points at the **same** `/data/tracker.db` on the shared volume via `--db`
(the worker's CLI contract — it does not read `DEADLOCK_DB`). To register your
account so there's something to ingest, run a one-off against the running stack:

```sh
docker compose run --rm worker python -m ingest --db /data/tracker.db \
  add-account <account-id-or-SteamID64-or-profile-url> --self
```

### Management UI (optional)

The management screens (Accounts importer, Era manager) are hidden in the frontend
unless it's **built** with `VITE_OWNER=true` — a nav convenience. The runtime gate is
Steam login (`DEADLOCK_BASE_URL`): with auth on, the writes those screens make
require a logged-in session; with auth off they're open only when
`DEADLOCK_OPEN_WRITES=1` (otherwise every write is `403`).

> The frontend's login/logout UI and its handling of this flag are finished in
> Phase 3; today the build flag just controls nav visibility.

```sh
docker compose build --build-arg VITE_OWNER=true
```

See [frontend/.env.example](../frontend/.env.example) for the frontend-side flag.

## Run without Docker (bare metal)

```sh
# 1. Build the frontend (FastAPI serves frontend/dist/).
npm --prefix frontend ci
npm --prefix frontend run build

# 2. Install runtime deps and migrate the DB.
pip install -r requirements.txt
python -m tracker.migrate data/tracker.db          # DEADLOCK_DB default is data/tracker.db

# 3. Serve the API + SPA on one origin.
uvicorn api.app:app --host 0.0.0.0 --port 8000

# 4. In a second process, run the worker against the same DB.
python -m ingest --db data/tracker.db run-daemon
```
