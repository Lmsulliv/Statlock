-- Schema v9: manual display-name labels, keyed (owner_id, account_id).
--
-- owner_id 0 = GLOBAL_OWNER sentinel; real user ids populate it later with no
-- schema change (the per-user seam). account_labels is the single source of
-- manual names; we seed it from the existing tracked_accounts.display_name so no
-- name is lost when the resolver stops reading that column. updated_at is NULL
-- for these migration-seeded rows; the rename API stamps it on every write.
CREATE TABLE account_labels (
    owner_id     INTEGER NOT NULL DEFAULT 0,
    account_id   INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    updated_at   TEXT,                       -- ISO 8601; NULL for migration-seeded rows
    PRIMARY KEY (owner_id, account_id)
);

INSERT INTO account_labels (owner_id, account_id, display_name, updated_at)
SELECT 0, account_id, display_name, NULL
  FROM tracked_accounts
 WHERE display_name IS NOT NULL AND TRIM(display_name) <> '';
