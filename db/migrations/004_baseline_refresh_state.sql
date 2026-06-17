-- Schema v4: per-era baseline refresh tracking (staggered refresh by mutability).

-- The nightly job no longer refreshes every era span every run. It refreshes the
-- open era nightly, recently-closed eras weekly, older closed eras and the
-- all-time sentinel monthly. This table records when each era's baselines were
-- last actually fetched from the API (NOT carried-forward), which the cadence
-- check reads to decide whether a span is due. era_id 0 is the all-time
-- sentinel (not a patch_eras row), so this is its own table keyed by era_id
-- rather than a column on patch_eras.
CREATE TABLE baseline_refresh_state (
    era_id            INTEGER PRIMARY KEY,   -- patch_eras.era_id, or 0 = all-time
    last_refreshed_at TEXT NOT NULL          -- ISO 8601 of the last real fetch
);
