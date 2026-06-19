-- Deadlock Stat Tracker: SQLite schema
-- Deviations from docs/data-model.md are each tagged [AF#N] referencing
-- the finding number in docs/api-findings.md that motivated the change.

-- ── Reference tables ────────────────────────────────────────────────────────

CREATE TABLE heroes (
    hero_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    image_url    TEXT,
    fetched_at   TEXT NOT NULL          -- ISO 8601
);

CREATE TABLE items (
    item_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    tier         INTEGER,               -- soul cost tier 1–5
    slot_type    TEXT,                  -- weapon / vitality / spirit
    image_url    TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE patch_eras (
    era_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT NOT NULL,
    started_at   TEXT NOT NULL,         -- ISO 8601; era covers [started_at, next era)
    UNIQUE (started_at)
);

-- ── Core match tables ────────────────────────────────────────────────────────

-- [AF#3] patch_id dropped: no game version field exists in match metadata.
-- [AF#1] average_badge split into two team columns; no per-player badge field.
-- Matches bind to eras via era_id, recomputed from start_time when eras change.
CREATE TABLE matches (
    match_id              INTEGER PRIMARY KEY,
    start_time            TEXT NOT NULL,       -- ISO 8601 (converted from unix int)
    duration_s            INTEGER NOT NULL,
    game_mode             TEXT,
    winning_team          INTEGER NOT NULL,    -- 0 = Amber, 1 = Sapphire
    era_id                INTEGER REFERENCES patch_eras(era_id),
    average_badge_team0   INTEGER,
    average_badge_team1   INTEGER,
    raw_json              TEXT NOT NULL,
    ingested_at           TEXT NOT NULL
);

-- [AF#2] party_id dropped: absent from metadata (AF finding #2).
-- [AF#4] lane stored as INTEGER (assigned_lane field), not TEXT.
-- [AF#5] player_damage/obj_damage/healing derived from last stats[] entry; may be NULL.
-- [AF#1] badge column dropped: no per-player rank in metadata.
CREATE TABLE match_players (
    match_id        INTEGER NOT NULL REFERENCES matches(match_id),
    account_id      INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL REFERENCES heroes(hero_id),
    team            INTEGER NOT NULL,
    lane            INTEGER,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    net_worth       INTEGER,
    last_hits       INTEGER,
    denies          INTEGER,
    player_damage   INTEGER,
    obj_damage      INTEGER,
    healing         INTEGER,
    won             INTEGER NOT NULL,   -- denormalized: team == winning_team
    PRIMARY KEY (match_id, account_id)
);

-- Per-match rank for tracked accounts only; sourced from mmr-history endpoint.
CREATE TABLE account_rank_history (
    account_id   INTEGER NOT NULL,
    match_id     INTEGER NOT NULL,
    badge        INTEGER,
    PRIMARY KEY (account_id, match_id)
);

CREATE INDEX idx_mp_account ON match_players(account_id);
CREATE INDEX idx_mp_hero    ON match_players(hero_id);

-- [AF#6] sold → sold_time_s INTEGER: API provides sold_time_s (0 = never sold).
CREATE TABLE match_item_purchases (
    match_id        INTEGER NOT NULL,
    account_id      INTEGER NOT NULL,
    item_id         INTEGER NOT NULL REFERENCES items(item_id),
    purchase_time_s INTEGER,
    sold_time_s     INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, account_id, item_id),
    FOREIGN KEY (match_id, account_id)
        REFERENCES match_players(match_id, account_id)
);

-- ── Global baselines ─────────────────────────────────────────────────────────

-- [AF#6] rank_bracket TEXT → badge_min/badge_max INTEGER (numeric 0–116).
-- [AF#5-era] era_id=0 is the all-time sentinel (AUTOINCREMENT starts at 1).
--            NULL in a composite PK is non-deduplicating in SQLite; 0 avoids that.
CREATE TABLE baseline_hero_matchups (
    snapshot_id     INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    enemy_hero_id   INTEGER NOT NULL,
    era_id          INTEGER NOT NULL DEFAULT 0,   -- 0 = all-time
    badge_min       INTEGER NOT NULL DEFAULT 0,
    badge_max       INTEGER NOT NULL DEFAULT 116,
    wins            INTEGER NOT NULL,
    matches         INTEGER NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max)
);

-- [AF#6] rank_bracket TEXT → badge_min/badge_max INTEGER.
CREATE TABLE baseline_hero_item_stats (
    snapshot_id     INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    era_id          INTEGER NOT NULL DEFAULT 0,
    badge_min       INTEGER NOT NULL DEFAULT 0,
    badge_max       INTEGER NOT NULL DEFAULT 116,
    wins            INTEGER NOT NULL,
    matches         INTEGER NOT NULL,
    avg_purchase_s  REAL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, item_id, era_id, badge_min, badge_max)
);

CREATE TABLE baseline_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT NOT NULL,
    notes           TEXT
);

-- ── Ingestion state ──────────────────────────────────────────────────────────

CREATE TABLE tracked_accounts (
    account_id      INTEGER PRIMARY KEY,
    display_name    TEXT,
    is_self         INTEGER DEFAULT 0,
    added_at        TEXT NOT NULL
);

CREATE TABLE sync_state (
    account_id          INTEGER PRIMARY KEY REFERENCES tracked_accounts(account_id),
    last_match_id       INTEGER,
    last_synced_at      TEXT
);

-- next_retry_at added: referenced by drain loop in ingestion-spec.md.
CREATE TABLE fetch_queue (
    match_id        INTEGER PRIMARY KEY,
    discovered_at   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | fetched | failed | unavailable | deferred
                    -- (deferred = not yet parsed upstream; see migration 007.
                    --  deferred_since is added by migration 007, not here.)
    attempts        INTEGER DEFAULT 0,
    last_attempt_at TEXT,
    next_retry_at   TEXT,
    last_error      TEXT
);

-- Archive for all raw API responses (hard rule 2: archive before parsing).
-- Matches already store raw_json; this covers assets, baselines, etc.
CREATE TABLE raw_api_responses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

-- ── Derived views ─────────────────────────────────────────────────────────────

-- [AF#3] Rewritten to use matches.era_id directly instead of the
-- impossible JOIN through a non-existent patches table.
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
WHERE me.account_id IN (SELECT account_id FROM tracked_accounts WHERE is_self = 1)
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
    ON ip.match_id = mp.match_id AND ip.account_id = mp.account_id
JOIN matches m ON m.match_id = mp.match_id
WHERE mp.account_id IN (SELECT account_id FROM tracked_accounts WHERE is_self = 1)
GROUP BY mp.account_id, mp.hero_id, ip.item_id, m.era_id;
