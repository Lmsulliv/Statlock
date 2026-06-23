# Deploying behind a single origin

In production the FastAPI app serves **both** the JSON API (under `/api/*`) and
the built React SPA (everything else) from one origin. There is no separate
frontend server and no CORS — the browser sees a single host. The ingestion
worker runs alongside the API as a second process sharing the same SQLite file.

> Authentication is **Steam OpenID**, opt-in via `DEADLOCK_BASE_URL`. Set it to the
> app's public origin to require login (and CSRF) on every write. Leave it unset for
> a private/local deploy and the app runs as a single user with writes open. Set it
> before exposing the app publicly.

## Environment variables

| Variable            | Used by | Required | Meaning |
|---------------------|---------|----------|---------|
| `DEADLOCK_DB`       | API     | No (image defaults to `/data/tracker.db`) | Path to the SQLite database the API reads. The worker uses its own `--db` flag instead (see below). |
| `DEADLOCK_BASE_URL` | API     | No (unset → auth off, single-user, writes open) | The app's public origin (e.g. `https://stats.example.com`). When set, Steam login is required: writes go through `require_user` (401 without a session) and are CSRF-protected. Steam redirects back to `<base>/api/auth/callback`. Use `https://` in production so the login cookies are `Secure`. |
| `STEAM_API_KEY`     | Worker  | No (clean no-op when unset) | Steam Web API key for persona (display-name) enrichment during worker maintenance. Without it, accounts fall back to bare ids. (Not needed for login — Steam OpenID doesn't use it.) |

All three are read fresh from the environment at runtime (`api/config.py`), so a
deploy sets them without any code change.

## Run with Docker Compose (recommended)

The image is multi-stage: a Node stage builds `frontend/dist`, then a Python
stage installs `requirements.txt` and serves the API + built SPA with uvicorn.
[docker-compose.yml](../docker-compose.yml) defines two services — `api` and
`worker` — sharing one named volume (`deadlock-data`) for the database.

1. Create a `.env` next to `docker-compose.yml` for config/secrets:

   ```dotenv
   DEADLOCK_BASE_URL=https://stats.example.com   # set to require Steam login; omit for local single-user
   STEAM_API_KEY=your-steam-web-api-key          # optional
   ```

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
now Steam login (`DEADLOCK_BASE_URL`): with auth on, the writes those screens make
require a logged-in session; with auth off (local), they're open.

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
