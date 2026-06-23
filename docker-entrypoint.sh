#!/bin/sh
set -e

# Apply any pending schema steps before serving. tracker/migrate.py is
# idempotent and safe to run on every boot, so the API container works
# standalone (no separate migrate step) and the worker container's re-run is a
# harmless no-op. Both share the same DB file on the mounted volume.
python -m tracker.migrate "${DEADLOCK_DB:-/data/tracker.db}"

# Hand off (PID 1) to the container's command: uvicorn for the API, or the
# worker command from docker-compose.yml.
exec "$@"
