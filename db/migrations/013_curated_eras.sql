-- Schema v13: reseed patch_eras to a curated list of 12 real Deadlock patches.
--
-- Before: a single placeholder era ("Initial era", 1970-01-01) meant every match
-- fell in one bucket and era-scoped views were meaningless. After: 12 curated
-- eras (Major Map Rework .. Minor Update Jun 11) so the era selector and per-era
-- baselines line up with actual patch windows.
--
-- This is a destructive, data-mutating step: it replaces every stored era,
-- re-bins every match against the new boundaries, and drops the now-orphaned
-- per-era FETCHED baselines (hero matchups + item stats) and their refresh state
-- so the next maintenance pass rebuilds them cleanly. The performance / laning /
-- death-timing baselines derive live from local rows, so re-binning matches
-- updates them for free -- nothing to delete there.
--
-- Ordering matters: connect() runs with foreign_keys ON and matches.era_id
-- REFERENCES patch_eras(era_id), so we cannot delete an era a match still points
-- at. We release the child references first (set matches.era_id = NULL), THEN
-- delete the now-unreferenced parent rows, insert the curated set, and re-bin.
--
-- era_id = 0 is the all-time sentinel (NOT a patch_eras row -- AUTOINCREMENT
-- starts at 1, and a NULL in a composite PK is non-deduplicating in SQLite, so 0
-- is used instead). The orphan cleanup must preserve it, hence the "era_id != 0"
-- guard on each baseline DELETE.
--
-- Boundary convention: started_at values are midnight UTC. A match played on a
-- patch's release day but before the patch shipped buckets into the new era.
-- Matches before the first era (2025-02-25) get era_id = NULL and appear only in
-- all-time views, never an era window -- "beginning with Major Map Rework".

-- a. Release the references so the parent rows become deletable.
UPDATE matches SET era_id = NULL;

-- b. Delete the now-unreferenced eras.
DELETE FROM patch_eras;

-- c. Insert the 12 curated eras, in order. era_id is AUTOINCREMENT, so the new
--    rows take fresh ids continuing from sqlite_sequence; nothing depends on the
--    specific integers (reads order by started_at and join via the FK).
INSERT INTO patch_eras(label, started_at) VALUES
    ('Major Map Rework',          '2025-02-25T00:00:00Z'),
    ('Major Item Rework',         '2025-05-08T00:00:00Z'),
    ('Six New Heroes',            '2025-09-06T00:00:00Z'),
    ('Old Gods, New Blood',       '2026-01-21T00:00:00Z'),
    ('Gameplay Update (Apr 10)',  '2026-04-10T00:00:00Z'),
    ('Gameplay Update (Apr 30)',  '2026-04-30T00:00:00Z'),
    ('Urn Update 1',              '2026-05-22T00:00:00Z'),
    ('Urn Update 2',              '2026-05-25T00:00:00Z'),
    ('Urn Update 3',              '2026-05-28T00:00:00Z'),
    ('Gameplay Update (May 31)',  '2026-05-31T00:00:00Z'),
    ('Urn Update 4',              '2026-06-04T00:00:00Z'),
    ('Minor Update (Jun 11)',     '2026-06-11T00:00:00Z');

-- d. Re-bin every match to the latest era starting at or before its start_time.
--    Matches before the first era stay NULL (the subquery returns no row).
--    Mirrors api/service.py::rebin_eras and ingest/parse.py::era_id_for.
UPDATE matches SET era_id = (
    SELECT e.era_id FROM patch_eras e
    WHERE e.started_at <= matches.start_time
    ORDER BY e.started_at DESC LIMIT 1
);

-- e. Drop orphaned per-era fetched baselines + refresh state so the next
--    maintenance run rebuilds them, keeping the all-time sentinel (era_id = 0).
DELETE FROM baseline_hero_matchups
    WHERE era_id NOT IN (SELECT era_id FROM patch_eras) AND era_id != 0;
DELETE FROM baseline_hero_item_stats
    WHERE era_id NOT IN (SELECT era_id FROM patch_eras) AND era_id != 0;
DELETE FROM baseline_refresh_state
    WHERE era_id NOT IN (SELECT era_id FROM patch_eras) AND era_id != 0;
