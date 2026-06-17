-- Schema v5: make player_slot (1-12, always present, unique within a match) the
-- per-match player identity instead of account_id.
--
-- Why: private-profile players return account_id = 0, and a match can hold up to
-- six of them. With PK (match_id, account_id) the second zero collides and ingest
-- dies with IntegrityError. player_slot is the only field the API guarantees to
-- be present and unique per match, so it is the honest match-local key. This also
-- gives kill attribution (a later phase) the stable slot identity it needs, since
-- death_details reference killers by killer_player_slot, not account.
--
-- SQLite can't ALTER a primary key, so this is the documented table-rebuild
-- (create new, copy, drop, rename). Both views (v_my_matchups, v_my_item_stats)
-- reference match_players, so they are dropped first and recreated last -- a view
-- left dangling over a dropped table breaks the next schema operation. foreign_keys
-- is ON per connection, so we toggle it OFF for the rebuild and back ON after (the
-- official procedure). PRAGMA works here because executescript runs in autocommit,
-- with no open transaction.

PRAGMA foreign_keys = OFF;

-- ── Build the new tables (backfilled) before dropping anything ────────────────

-- match_players: add player_slot, re-key to (match_id, player_slot). account_id
-- stays NOT NULL (0 is valid for anonymized players) but is no longer unique
-- within a match.
CREATE TABLE match_players_new (
    match_id        INTEGER NOT NULL REFERENCES matches(match_id),
    player_slot     INTEGER NOT NULL,
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
    won             INTEGER NOT NULL,
    PRIMARY KEY (match_id, player_slot)
);

-- Backfill player_slot from each match's archived raw_json. json_each walks the
-- players[] array; we match each existing row to the JSON player with the same
-- account_id and read that player's slot. Real account_ids map uniquely; a single
-- account_id = 0 maps to the one slot with account_id = 0 (matches with 2+ zeros
-- never got stored, so existing data is unambiguous). If a row can't be mapped the
-- subquery yields NULL, which violates player_slot NOT NULL and aborts the
-- migration -- failing loudly instead of guessing.
INSERT INTO match_players_new (match_id, player_slot, account_id, hero_id, team,
    lane, kills, deaths, assists, net_worth, last_hits, denies, player_damage,
    obj_damage, healing, won)
SELECT mp.match_id,
       (SELECT CAST(json_extract(je.value, '$.player_slot') AS INTEGER)
          FROM json_each(m.raw_json, '$.match_info.players') je
         WHERE CAST(json_extract(je.value, '$.account_id') AS INTEGER)
               = mp.account_id),
       mp.account_id, mp.hero_id, mp.team, mp.lane, mp.kills, mp.deaths,
       mp.assists, mp.net_worth, mp.last_hits, mp.denies, mp.player_damage,
       mp.obj_damage, mp.healing, mp.won
FROM match_players mp
JOIN matches m ON m.match_id = mp.match_id;

-- match_item_purchases: re-key to (match_id, player_slot, item_id). account_id is
-- kept as a plain column. Map each old purchase to its slot via match_players_new,
-- joining on (match_id, account_id) -- unique for existing data, same invariant.
CREATE TABLE match_item_purchases_new (
    match_id        INTEGER NOT NULL,
    player_slot     INTEGER NOT NULL,
    account_id      INTEGER NOT NULL,
    item_id         INTEGER NOT NULL REFERENCES items(item_id),
    purchase_time_s INTEGER,
    sold_time_s     INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, player_slot, item_id),
    FOREIGN KEY (match_id, player_slot)
        REFERENCES match_players(match_id, player_slot)
);

INSERT INTO match_item_purchases_new (match_id, player_slot, account_id, item_id,
    purchase_time_s, sold_time_s)
SELECT ip.match_id, mp.player_slot, ip.account_id, ip.item_id,
       ip.purchase_time_s, ip.sold_time_s
FROM match_item_purchases ip
JOIN match_players_new mp
    ON mp.match_id = ip.match_id AND mp.account_id = ip.account_id;

-- ── Drop the dependent views, then swap the tables ────────────────────────────
DROP VIEW v_my_item_stats;
DROP VIEW v_my_matchups;

DROP TABLE match_item_purchases;   -- child first
DROP TABLE match_players;

ALTER TABLE match_players_new RENAME TO match_players;
ALTER TABLE match_item_purchases_new RENAME TO match_item_purchases;

-- Indexes are dropped with the old table; recreate them on the new one.
CREATE INDEX idx_mp_account ON match_players(account_id);
CREATE INDEX idx_mp_hero    ON match_players(hero_id);

-- ── Recreate the views ────────────────────────────────────────────────────────
-- v_my_matchups is unchanged: it self-joins match_players on match_id/team and
-- never keys on account_id between tables.
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

-- v_my_item_stats now joins the two tables on (match_id, player_slot).
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
WHERE mp.account_id IN (SELECT account_id FROM tracked_accounts WHERE is_self = 1)
GROUP BY mp.account_id, mp.hero_id, ip.item_id, m.era_id;

PRAGMA foreign_key_check;
PRAGMA foreign_keys = ON;
