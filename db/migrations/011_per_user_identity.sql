-- Schema v11: per-user identity. Replaces the single global "self" with real
-- users, the prerequisite for multi-user online.
--
-- Before: one tracked_accounts.is_self = 1 flag marked THE self account, and
-- manual name labels were keyed by account_labels.owner_id whose only value was
-- the GLOBAL_OWNER = 0 sentinel. Both are global; neither scales past one person.
--
-- After: a users table holds identities, and user_accounts is a join table (a
-- many-to-many link) mapping each user to the accounts they own, with is_self on
-- the link instead of on the account. account_labels.owner_id is renamed to a
-- real user_id. We seed a first user (id 1) and migrate all existing single-user
-- data onto it so nothing is orphaned; local/dev keeps running as this default
-- user until real auth (Phase 2). is_self stays as a now-vestigial column on
-- tracked_accounts -- user_accounts is authoritative from here on.
--
-- account_labels can't be ALTERed to rename its primary-key column, so it uses
-- the documented table-rebuild (create new, copy, drop, rename) with foreign_keys
-- toggled off for the swap, matching migration 005.

CREATE TABLE users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT                     -- ISO 8601; NULL for the seeded user, stamped on real signup
);

-- The default/local user. Seeded unconditionally so a fresh DB has an identity
-- even before any account is tracked.
INSERT INTO users (user_id, created_at) VALUES (1, NULL);

-- user_accounts: which accounts a user owns; is_self flags their primary one.
CREATE TABLE user_accounts (
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    account_id INTEGER NOT NULL,
    is_self    INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT,
    PRIMARY KEY (user_id, account_id)
);

-- Migrate every existing tracked account onto user 1, preserving its is_self flag
-- so resolve_self_account_id keeps returning the same account.
INSERT INTO user_accounts (user_id, account_id, is_self, added_at)
SELECT 1, account_id, is_self, added_at FROM tracked_accounts;

-- ── Re-key account_labels: owner_id -> user_id ────────────────────────────────
PRAGMA foreign_keys = OFF;

CREATE TABLE account_labels_new (
    user_id      INTEGER NOT NULL DEFAULT 1 REFERENCES users(user_id),
    account_id   INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    updated_at   TEXT,                       -- ISO 8601; NULL for migration-seeded rows
    PRIMARY KEY (user_id, account_id)
);

-- The GLOBAL_OWNER = 0 sentinel becomes user 1; any other owner_id is carried
-- through untouched (there are none today, but the mapping is explicit).
INSERT INTO account_labels_new (user_id, account_id, display_name, updated_at)
SELECT CASE WHEN owner_id = 0 THEN 1 ELSE owner_id END,
       account_id, display_name, updated_at
  FROM account_labels;

DROP TABLE account_labels;
ALTER TABLE account_labels_new RENAME TO account_labels;

-- ── Recreate the views to read self from user_accounts ────────────────────────
-- These views are vestigial (no query reads them; the live self path is
-- resolve_self_account_id), but they're kept consistent. A view can't take a
-- user parameter, so it filters on any user's self -- identical for one user.
DROP VIEW v_my_matchups;
DROP VIEW v_my_item_stats;

CREATE VIEW v_my_matchups AS
SELECT
    me.account_id,
    me.hero_id        AS my_hero,
    opp.hero_id       AS enemy_hero,
    m.era_id,
    COUNT(*)          AS games,
    SUM(me.won)       AS wins
FROM match_players me
JOIN match_players opp
    ON  opp.match_id = me.match_id
    AND opp.team    != me.team
JOIN matches m ON m.match_id = me.match_id
WHERE me.account_id IN (SELECT account_id FROM user_accounts WHERE is_self = 1)
GROUP BY me.account_id, me.hero_id, opp.hero_id, m.era_id;

CREATE VIEW v_my_item_stats AS
SELECT
    mp.account_id,
    mp.hero_id,
    ip.item_id,
    m.era_id,
    COUNT(*)                AS games,
    SUM(mp.won)             AS wins,
    AVG(ip.purchase_time_s) AS avg_purchase_s
FROM match_players mp
JOIN match_item_purchases ip
    ON ip.match_id = mp.match_id AND ip.player_slot = mp.player_slot
JOIN matches m ON m.match_id = mp.match_id
WHERE mp.account_id IN (SELECT account_id FROM user_accounts WHERE is_self = 1)
GROUP BY mp.account_id, mp.hero_id, ip.item_id, m.era_id;

PRAGMA foreign_key_check;
PRAGMA foreign_keys = ON;
