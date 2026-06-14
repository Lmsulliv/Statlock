-- Schema v3: rank reference tiers + a same-lane dimension on matchup baselines.

-- Rank tiers from /v1/assets/ranks (badge = tier*10 + subtier). We store the
-- semantic bits (name, color); the badge ART url is derived from the tier in
-- the read layer, not stored.
CREATE TABLE ranks (
    tier        INTEGER PRIMARY KEY,   -- 0 Obscurus .. 11 Eternus
    name        TEXT NOT NULL,
    color       TEXT,                  -- hex accent, e.g. #5CE9A9
    fetched_at  TEXT NOT NULL
);

-- Add same_lane to baseline_hero_matchups so the laning-phase (same-lane)
-- baseline can coexist with the overall one for the same
-- (snapshot, hero, enemy, era, bracket). It joins the PRIMARY KEY, and SQLite
-- can't ALTER a key, so rebuild the table. Existing rows become "overall"
-- (same_lane = 0). [AF#... hero-counter-stats?same_lane_filter=true]
CREATE TABLE baseline_hero_matchups_new (
    snapshot_id     INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    enemy_hero_id   INTEGER NOT NULL,
    era_id          INTEGER NOT NULL DEFAULT 0,
    badge_min       INTEGER NOT NULL DEFAULT 0,
    badge_max       INTEGER NOT NULL DEFAULT 116,
    same_lane       INTEGER NOT NULL DEFAULT 0,   -- 0 = overall, 1 = same-lane
    wins            INTEGER NOT NULL,
    matches         INTEGER NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max, same_lane)
);

INSERT INTO baseline_hero_matchups_new
    (snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max,
     same_lane, wins, matches, fetched_at)
SELECT snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max,
       0, wins, matches, fetched_at
FROM baseline_hero_matchups;

DROP TABLE baseline_hero_matchups;
ALTER TABLE baseline_hero_matchups_new RENAME TO baseline_hero_matchups;
