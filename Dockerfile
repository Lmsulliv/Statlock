# syntax=docker/dockerfile:1

# --- Stage 1: build the React/Vite frontend -------------------------------
FROM node:20-alpine AS frontend
WORKDIR /app/frontend

# Management UI is a build-time flag (nav convenience only; the real gate is the
# API's Steam login under DEADLOCK_BASE_URL). Off by default; enable with
# `docker build --build-arg VITE_OWNER=true ...`.
ARG VITE_OWNER=""
ENV VITE_OWNER=$VITE_OWNER

# Install from the lockfile first so this layer caches when only source changes.
# `npm ci` is reproducible and needs the committed package-lock.json.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build   # tsc && vite build -> /app/frontend/dist

# --- Stage 2: Python runtime serving the API + the built SPA --------------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# Default DB path; the entrypoint migrates this on boot and a volume mounted at
# /data persists it. Overridable at run time (see docs/deploy.md).
ENV DEADLOCK_DB=/data/tracker.db

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only what the API and the worker import at runtime. db/ ships because
# tracker/migrate.py reads schema.sql + migrations/ from it.
COPY api/ ./api/
COPY ingest/ ./ingest/
COPY stats/ ./stats/
COPY tracker/ ./tracker/
COPY db/ ./db/

# Built frontend from the node stage; api/app.py serves frontend/dist.
COPY --from=frontend /app/frontend/dist ./frontend/dist

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
# The entrypoint migrates the DB, then execs whatever command is given. Default
# is the API; the worker overrides the command (see docker-compose.yml).
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
