# Deadlock Stat Tracker: MVP Data Model

Scope of this document: the storage layer for the MVP slice — match ingestion, personal matchup win rates with proper confidence intervals, and personal-vs-global deltas. SQLite dialect, but nothing here is SQLite-specific enough to block a later move to Postgres.

## Design principles

1. **Store raw, derive everything else.** Every API response gets archived as raw JSON alongside the normalized columns. When you add a feature later (death timing, soul curves) you re-derive from local data instead of re-fetching, which matters given the rate limits you're already fighting on match report uploads.
2. **Matches are immutable, baselines are snapshots.** A finished match never changes, so the match tables are append-only. Global analytics *do* change as the API accumulates data, so baselines are versioned snapshots with a fetched_at timestamp.
3. **Store at the finest granularity, scope at query time.** Every match records its exact game build, and every player row records their badge at match time. Patch era and rank bracket are then just `WHERE` clauses, so the UI can offer "current era at my rank" as the smart default while "all time," "all ranks," or any custom rank range remain one filter change away. Nothing is pre-bucketed.
4. **Aggregates are computed at query time, not stored.** Your personal dataset is small (hundreds of matches, not millions). Views and application code can compute matchup tables on the fly, which means no cache invalidation bugs.

---

## Reference tables (from the Assets API)

```sql
CREATE TABLE heroes (
    hero_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    image_url    TEXT,
    fetched_at   TEXT NOT NULL          -- ISO 8601
);

CREATE TABLE items (
    item_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    tier         INTEGER,               -- soul cost tier
    slot_type    TEXT,                  -- weapon / vitality / spirit
    image_url    TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE patch_eras (
    era_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT NOT NULL,         -- e.g. "Urn rework era"
    started_at   TEXT NOT NULL
);

```

Refresh heroes/items on a schedule (weekly is plenty) and after any patch. `patches` can be seeded manually at first; the match metadata includes a game version you can map to it.

**Eras, not builds, are the analytical unit.** Deadlock ships minor builds constantly in early access, and treating each one as a fresh meta would shred your sample sizes for no benefit. An *era* is a manually curated span of builds judged to share the same meta: when a significant gameplay patch lands, add one `patch_eras` row, and every subsequent minor build maps to it. Matches always keep their exact `patch_id` (never throw away precision), so if you later decide a patch mattered more than you thought, you redraw the era boundaries and every stat recomputes correctly. Analytics queries filter and group by `era_id` by default.

## Core match tables

```sql
CREATE TABLE matches (
    match_id        INTEGER PRIMARY KEY,
    start_time      TEXT NOT NULL,
    duration_s      INTEGER NOT NULL,
    game_mode       TEXT,
    winning_team    INTEGER NOT NULL,   -- 0 = Amber, 1 = Sapphire
    patch_id        TEXT REFERENCES patches(patch_id),
    era_id          INTEGER REFERENCES patch_eras(era_id)
    average_badge_team0   INTEGER,      -- team 0 average rank if provided
    average_badge_team1   INTEGER,      -- team 1 average rank if provided
    raw_json        TEXT NOT NULL,      -- full metadata response, archived
    ingested_at     TEXT NOT NULL
);

CREATE TABLE match_players (
    match_id        INTEGER NOT NULL REFERENCES matches(match_id),
    account_id      INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL REFERENCES heroes(hero_id),
    team            INTEGER NOT NULL,
    lane            INTEGER,               -- if metadata provides assigned lane
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    net_worth       INTEGER,            -- final souls
    last_hits       INTEGER,
    denies          INTEGER,
    player_damage   INTEGER,
    obj_damage      INTEGER,
    healing         INTEGER,
    won             INTEGER NOT NULL,   -- denormalized: team == winning_team
    PRIMARY KEY (match_id, account_id)
);

CREATE TABLE account_rank_history ( -- Populated from mmr-history for tracked accounts only;
-- per-player ranks for other lobby members don't exist (see api-findings.md)
    account_id   INTEGER NOT NULL,
    match_id     INTEGER NOT NULL,
    badge        INTEGER,
    PRIMARY KEY (account_id, match_id)
);

CREATE INDEX idx_mp_account ON match_players(account_id);
CREATE INDEX idx_mp_hero    ON match_players(hero_id);

CREATE TABLE match_item_purchases (
    match_id        INTEGER NOT NULL,
    account_id      INTEGER NOT NULL,
    item_id         INTEGER NOT NULL REFERENCES items(item_id),
    purchase_time_s INTEGER,            -- seconds into match, NULL if unknown
    sold            INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, account_id, item_id),
    FOREIGN KEY (match_id, account_id)
        REFERENCES match_players(match_id, account_id)
);
```

Notes:

- `won` is technically redundant but it appears in nearly every analytical query, so denormalizing it is worth it.
- `match_players` holds all 12 players, not just you. That single decision is what makes matchup analysis, lane opponent analysis, and party detection possible without re-fetching anything.
- Column availability depends on what the match metadata endpoint actually returns for each field. Treat the nullable columns as best-effort and lean on `raw_json` for anything missed at first pass.

## Global baselines (from the Analytics API)

```sql
CREATE TABLE baseline_hero_matchups (
    snapshot_id     INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    enemy_hero_id   INTEGER NOT NULL,
    era_id          INTEGER,            -- NULL = all-time baseline
    badge_min INTEGER,               -- finest bracket the API offers
    badge_max INTEGER,
    wins            INTEGER NOT NULL,
    matches         INTEGER NOT NULL,   -- sample size: keep it, never store only the %
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, enemy_hero_id, era_id, rank_bracket)
);

CREATE TABLE baseline_hero_item_stats (
    snapshot_id     INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    era_id          INTEGER,
    rank_bracket    TEXT,
    wins            INTEGER NOT NULL,
    matches         INTEGER NOT NULL,
    avg_purchase_s  REAL,               -- purchase timing baseline
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, item_id, era_id, rank_bracket)
);

CREATE TABLE baseline_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT NOT NULL,
    notes           TEXT
);
```

Two rules here. First: **store wins and matches, never just a win rate.** You need the raw counts to compute confidence intervals and to do Bayesian shrinkage of your personal stats toward the global prior. A percentage alone throws that information away.

Second, and it's the same idea wearing different clothes: **fetch baselines at the finest rank bracket the API offers, and build coarser scopes by summing counts.** An "all ranks" baseline is `SUM(wins) / SUM(matches)` across brackets; an "Archon through Oracle" baseline is the same sum over a subset. This only works because rule one preserved the counts, and it means the baseline tables support the exact same flexible scoping (single bracket, custom range, everything) as your personal data without any extra fetching.

Redrawing era boundaries triggers a re-bin: a single UPDATE recomputing era_id from start_time, no re-ingestion

## Ingestion state

```sql
CREATE TABLE tracked_accounts (
    account_id      INTEGER PRIMARY KEY,
    display_name    TEXT,
    is_self         INTEGER DEFAULT 0,
    added_at        TEXT NOT NULL
);

CREATE TABLE sync_state (
    account_id          INTEGER PRIMARY KEY REFERENCES tracked_accounts(account_id),
    last_match_id       INTEGER,        -- high-water mark for incremental pulls
    last_synced_at      TEXT
);

CREATE TABLE fetch_queue (
    match_id        INTEGER PRIMARY KEY,
    discovered_at   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | fetched | failed | unavailable
    attempts        INTEGER DEFAULT 0,
    last_attempt_at TEXT,
    last_error      TEXT
);
```

This is what makes the automated, steady-pace pulling you described work. The loop is:

1. **Discover.** Hit match history for each tracked account, insert any unseen match IDs into `fetch_queue`, advance `last_match_id`.
2. **Drain.** A worker pulls `pending` rows at a polite fixed rate (e.g. one metadata fetch every few seconds with jitter), writes to the match tables, and marks the row `fetched`.
3. **Retry with backoff.** Failed rows increment `attempts`; give up after N tries and mark `unavailable`. This handles the exact situation you're in now where old matches aren't fetchable yet because of Valve's match report unlock throttle. A nightly job can flip stale `unavailable` rows back to `pending`, so when you unlock more old reports, the tracker picks them up automatically with zero manual work.

## Derived views

```sql
-- Personal record against each enemy hero
CREATE VIEW v_my_matchups AS
SELECT
    me.account_id,
    me.hero_id        AS my_hero,
    opp.hero_id       AS enemy_hero,
    p.era_id,
    COUNT(*)          AS games,
    SUM(me.won)       AS wins
FROM match_players me
JOIN match_players opp
    ON  opp.match_id = me.match_id
    AND opp.team    != me.team
JOIN matches m ON m.match_id = me.match_id
JOIN patches p ON p.patch_id = m.patch_id
WHERE me.account_id IN (SELECT account_id FROM tracked_accounts WHERE is_self = 1)
GROUP BY me.account_id, me.hero_id, opp.hero_id, p.era_id;

-- Personal item record per hero
CREATE VIEW v_my_item_stats AS
SELECT
    mp.account_id,
    mp.hero_id,
    ip.item_id,
    p.era_id,
    COUNT(*)                AS games,
    SUM(mp.won)             AS wins,
    AVG(ip.purchase_time_s) AS avg_purchase_s
FROM match_players mp
JOIN match_item_purchases ip
    ON ip.match_id = mp.match_id AND ip.account_id = mp.account_id
JOIN matches m ON m.match_id = mp.match_id
JOIN patches p ON p.patch_id = m.patch_id
WHERE mp.account_id IN (SELECT account_id FROM tracked_accounts WHERE is_self = 1)
GROUP BY mp.account_id, mp.hero_id, ip.item_id, p.era_id;
```

These views group at era granularity; "all time" is a further `SUM` over eras in application code, and rank-range filtering happens by adding a badge predicate before grouping. The point stands either way: scope is something callers choose per query, never something baked into stored data.

## Statistics layer (application code, not SQL)

Two functions, both straight out of IEE 380 territory:

**Wilson score interval** for any personal win rate. With wins `w` out of `n` games at 95% confidence (z = 1.96):

```
p̂ = w / n
center = (p̂ + z²/2n) / (1 + z²/n)
halfwidth = (z / (1 + z²/n)) · sqrt(p̂(1−p̂)/n + z²/4n²)
```

Display the interval, not just the point estimate. Six games against a hero gives an interval so wide it spans most of [0, 1], and showing that honestly is the feature.

**Shrinkage toward the global prior** for the personal-vs-global delta. Treat the global baseline as a Beta prior and your games as evidence:

```
prior strength k (try k = 10 to start)
α = k · p_global,  β = k · (1 − p_global)
p_adjusted = (w + α) / (n + α + β)
delta = p_adjusted − p_global
```

With 3 games your adjusted rate barely moves off the global average; with 40 games your own data dominates. The UI surfaces `delta` ranked by magnitude, filtered to matchups/items where the Wilson interval excludes the global rate, and that filtered list is your "solid direction of improvement" screen.

## What's deliberately not here yet

Death timestamps and positions, soul curves over time, ability builds, and per-lane stats. All of them slot in as new tables keyed on `(match_id, account_id)` without touching anything above, and `raw_json` means some can be backfilled without re-fetching. That's the test the schema needed to pass.
