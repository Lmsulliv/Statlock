-- Schema v18: prioritized, fair, newest-first draining.
--
-- Why: DrainWorker._next_row was global FIFO by discovered_at with an ASCENDING
-- match_id tiebreak, so a fresh account import drained its OLDEST matches first
-- (the least relevant ones, and the most likely to 400 on salts), and one user's
-- 300-match import starved every account queued after it. The drain loop now
-- picks work in priority tiers with per-account round-robin fairness and
-- newest-first ordering inside each account (see docs/ingestion-spec.md).
--
-- fetch_queue.priority: 1 = fresh user-facing work (an account's newest matches
-- on first import, plus every newly played match), 0 = everything else. The
-- drain loop serves priority > 0 rows first.
--
-- fetch_queue.discovered_for_account: the account whose discovery queued the
-- row. First discoverer wins -- discovery inserts stay INSERT OR IGNORE, so a
-- match two tracked players share keeps its original owner and priority. NULL
-- on rows that predate this migration (they drain in the priority-0 tier).
--
-- sync_state.last_drained_at: the round-robin cursor. Among accounts owning
-- eligible rows, the drain loop serves the one whose last_drained_at is oldest
-- (NULL sorts first), so two simultaneous bulk imports interleave instead of
-- one starving the other. Stamped after every fetch attempt for an owned row.
--
-- New status 'backfill': the older remainder of a first import -- drained only
-- when nothing pending or due-failed is eligible, and excluded from queue depth
-- like 'deferred'. The status column is free text (compare migration 007's
-- 'deferred'), so the new value needs no DDL.

ALTER TABLE fetch_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fetch_queue ADD COLUMN discovered_for_account INTEGER;
ALTER TABLE sync_state ADD COLUMN last_drained_at TEXT;

-- Serves the per-account fairness grouping and the "newest eligible row for
-- this account" lookup, same rationale as migration 016's drain index.
CREATE INDEX idx_fetch_queue_account
    ON fetch_queue(status, discovered_for_account, match_id);
