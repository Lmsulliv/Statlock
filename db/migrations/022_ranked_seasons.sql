-- Schema v22: ranked seasons + one ranks ladder per rank system.
--
-- The badge encoding (tier*10 + subtier) is shared by two rank ladders with
-- different names: before the Jul 30 2026 Matchmaking Update badge 55 was
-- Ritualist 5, after it Mystic 5 (docs/api-findings.md "Ranked seasons and rank
-- systems", spike 15). A single ranks table that refresh_assets overwrote by tier
-- mislabeled every pre-cutover rank point, so ranks is now keyed by
-- (rank_system, tier) and each season says which system it uses.

-- a. Seasons. Each covers [started_at, next started_at) -- the same half-open
--    model as patch_eras. The two Beta seasons' starts are the API's own
--    /v1/assets/ranked-seasons start_timestamp values; "legacy" (which that
--    endpoint does not list) starts where our first rank history does. Only
--    api/service.season_for reads these dates. A new season is one more INSERT
--    in a later migration.
CREATE TABLE ranked_seasons (
    slug         TEXT PRIMARY KEY,                -- also the ?rank_view= value
    label        TEXT NOT NULL,
    rank_system  TEXT NOT NULL CHECK (rank_system IN ('legacy', 'seasonal')),
    started_at   TEXT NOT NULL UNIQUE             -- ISO-8601 UTC
);

INSERT INTO ranked_seasons(slug, label, rank_system, started_at) VALUES
    ('legacy',        'Legacy ranks',  'legacy',   '2024-10-10T00:00:00+00:00'),
    ('beta-season-1', 'Beta Season 1', 'seasonal', '2026-07-30T17:00:00+00:00'),
    ('beta-season-2', 'Beta Season 2', 'seasonal', '2026-11-05T20:00:00+00:00');

-- b. Rebuild ranks with rank_system in the key (SQLite can't ALTER a primary
--    key). No DEFAULT on rank_system: every writer must say which ladder it means.
--    Nothing has a foreign key to ranks, so the rebuild needs no FK juggling.
CREATE TABLE ranks_new (
    rank_system  TEXT NOT NULL CHECK (rank_system IN ('legacy', 'seasonal')),
    tier         INTEGER NOT NULL,                -- 0 Obscurus .. 11 Eternus
    name         TEXT NOT NULL,
    color        TEXT,                            -- hex accent, e.g. #5CE9A9
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (rank_system, tier)
);

-- c. Keep the existing rows (values AND fetched_at). refresh_assets writes all
--    12 tiers in one go, so the table holds one ladder; tell which by the tiers
--    whose names differ between the two (3-7). A table holding only the shared
--    tiers is called seasonal: those names and colors are identical either way.
INSERT INTO ranks_new(rank_system, tier, name, color, fetched_at)
SELECT CASE WHEN EXISTS (
            SELECT 1 FROM ranks
            WHERE (tier, name) IN (VALUES (3, 'Alchemist'), (4, 'Arcanist'),
                                          (5, 'Ritualist'), (6, 'Emissary'), (7, 'Archon')))
       THEN 'legacy' ELSE 'seasonal' END,
       tier, name, color, fetched_at
FROM ranks;

-- d. The legacy ladder, copied from the last archived pre-cutover
--    /v1/assets/ranks response (raw_api_responses id 3551). OR IGNORE: a
--    database that still holds its own legacy rows keeps them.
INSERT OR IGNORE INTO ranks_new(rank_system, tier, name, color, fetched_at) VALUES
    ('legacy',  0, 'Obscurus',  '#333333', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  1, 'Initiate',  '#6A3E1E', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  2, 'Seeker',    '#882355', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  3, 'Alchemist', '#5C6DAB', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  4, 'Arcanist',  '#719C47', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  5, 'Ritualist', '#DDA326', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  6, 'Emissary',  '#EE4F57', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  7, 'Archon',    '#B47FEB', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  8, 'Oracle',    '#955138', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy',  9, 'Phantom',   '#7C7C7C', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy', 10, 'Ascendant', '#C39751', '2026-07-29T01:56:01.908112+00:00'),
    ('legacy', 11, 'Eternus',   '#5CE9A9', '2026-07-29T01:56:01.908112+00:00');

DROP TABLE ranks;
ALTER TABLE ranks_new RENAME TO ranks;
