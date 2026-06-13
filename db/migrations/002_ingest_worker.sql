-- Schema v2: tables the ingestion worker needs.

-- Era candidates from patch-notes detection (docs/presentation-spec.md).
-- UNIQUE(post_url) makes detection idempotent: re-scanning the same Steam
-- News feed inserts each post at most once (INSERT OR IGNORE).
CREATE TABLE era_candidates (
    candidate_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    post_url      TEXT NOT NULL UNIQUE,
    post_title    TEXT,
    posted_at     TEXT NOT NULL,
    change_lines  INTEGER,
    score         REAL,
    status        TEXT NOT NULL DEFAULT 'pending'  -- pending | confirmed | dismissed
);

-- Small key-value store for worker progress that isn't per-account or
-- per-match (e.g. last_maintenance_at). Crash-safety rule 1: all progress
-- lives in the database, none in memory.
CREATE TABLE worker_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
