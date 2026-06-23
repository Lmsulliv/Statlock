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
    player_slot     INTEGER NOT NULL,   -- 1..12, always present, unique per match
    account_id      INTEGER NOT NULL,   -- 0 for private profiles (anonymized);
                                        -- NOT unique within a match
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
    PRIMARY KEY (match_id, player_slot)
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
    player_slot     INTEGER NOT NULL,
    account_id      INTEGER NOT NULL,   -- kept for convenience; not part of the key
    item_id         INTEGER NOT NULL REFERENCES items(item_id),
    purchase_time_s INTEGER,            -- seconds into match, NULL if unknown
    sold            INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, player_slot, item_id),
    FOREIGN KEY (match_id, player_slot)
        REFERENCES match_players(match_id, player_slot)
);
```

Notes:

- **`player_slot`, not `account_id`, is the per-match player key.** Players with
  private profiles return `account_id = 0`, and a single match can hold up to six
  of them, so `account_id` is not unique within a match and cannot identify a
  player. `player_slot` (1–12) is the only field the API guarantees present and
  unique per match — it is the honest match-local identity, and `death_details`
  reference killers by `killer_player_slot`, so kill attribution needs it too.
  Anonymized players (`account_id = 0`) are *excluded* from recurring co-player
  counts (not real people, and all of a lobby's zeros would collapse into one
  inflated group) but *kept* in hero matchups (an anonymized opponent still
  piloted a known hero). `account_id` stays on both tables for convenience.
- `won` is technically redundant but it appears in nearly every analytical query, so denormalizing it is worth it.
- `match_players` holds all 12 players, not just you. That single decision is what makes matchup analysis, lane opponent analysis, and party detection possible without re-fetching anything.
- Column availability depends on what the match metadata endpoint actually returns for each field. Treat the nullable columns as best-effort and lean on `raw_json` for anything missed at first pass.
- This document is the design intent; the live schema is in `db/schema.sql` +
  `db/migrations/`, which is authoritative. Pre-existing drift not touched by the
  player_slot change is left as-is here: `match_item_purchases.sold` is actually
  `sold_time_s INTEGER` in the schema [AF#6], and the `patches` table / `patch_id`
  columns referenced below were dropped in favour of `era_id` [AF#3].

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

## Per-user identity (schema v11)

```sql
CREATE TABLE users (
    user_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT                     -- NULL for the seeded user; stamped on real signup
);

-- A join table (many-to-many): which accounts a user owns, with their primary one
-- flagged is_self. This replaces the single global tracked_accounts.is_self flag.
CREATE TABLE user_accounts (
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    account_id INTEGER NOT NULL,
    is_self    INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT,
    PRIMARY KEY (user_id, account_id)
);
```

`tracked_accounts` stays the **global ingestion registry** (the worker fetches each
tracked account once, regardless of how many users own it). Ownership and the
"self" flag are per-user, so they live on `user_accounts`. `tracked_accounts.is_self`
is retained as a now-vestigial column (nothing reads it after v11).

Manual name labels are likewise per-user: `account_labels` is keyed by `user_id`
(renamed from the old `owner_id` / `GLOBAL_OWNER = 0` sentinel in v11). A user's
labels are private to that user.

**Why a join table (many-to-many) instead of a single `self_account_id` on `users`:**
a user can own several accounts (mains, smurfs) and we want the account switcher to
list exactly their accounts, with one flagged self — that is naturally a
user↔account link table, and it is what the Phase 3 per-user switcher reads with no
further migration.

**The default-user seam:** until real auth exists (Phase 2), there is no session to
identify the requester, so every resolver defaults to `DEFAULT_USER_ID = 1` (the
first user, seeded by the migration). The app therefore behaves exactly as it did
when "self" was global. Phase 2 introduces a session-backed dependency that supplies
the real user id per request; the resolver signatures already take `user_id`, so
that swap doesn't change the read queries.

## Authentication (schema v12)

```sql
ALTER TABLE users ADD COLUMN steam_account_id INTEGER;  -- 32-bit; NULL for the local/dev user
CREATE UNIQUE INDEX idx_users_steam ON users(steam_account_id);

CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,                          -- opaque random; the cookie value
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
```

Login is **Steam OpenID 2.0** (`api/auth.py`): a user signs in at Steam, we verify
the signed reply, and the returned SteamID64 is normalized to the same 32-bit
`steam_account_id` the importer uses — so a logged-in user is automatically linked
to their own Deadlock account (their self account in `user_accounts`).

Sessions are **server-side** (the `sessions` table) rather than a stateless signed
cookie, so logout truly revokes (delete the row) and sessions expire. The httpOnly
`session` cookie carries only the opaque token. Writes are CSRF-protected by a
**double-submit token**: a readable `csrf` cookie set at login, echoed by the SPA in
the `X-CSRF-Token` header and compared for equality server-side.

**Auth is opt-in** (`DEADLOCK_BASE_URL`). When unset (local/dev) the app runs as the
single default user with writes open — the original single-user workflow. When set,
`require_user` enforces a valid session (+ CSRF) on every write, and a user can only
write within their own identity because the write's `user_id` comes from the session,
never from client input.

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
WHERE me.account_id IN (SELECT account_id FROM user_accounts WHERE is_self = 1)
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
    ON ip.match_id = mp.match_id AND ip.player_slot = mp.player_slot
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

**Continuous-metric mean and interval** for the metrics that aren't win/loss. Net worth, last hits, denies, player damage, obj damage, healing, and the kills/deaths/assists behind KDA are *continuous*, so Wilson and the Beta prior above — both binomial, with variance fixed by the rate — do not apply to them. A mean instead gets a **Student-t interval** (`stats.mean_interval`). With a sample of `n` values, mean `x̄`, and sample standard deviation `s` (the `n−1` denominator) at confidence `c`:

```
SE = s / √n
halfwidth = t(df = n−1, c) · SE
interval = x̄ ± halfwidth
```

A t-interval is the small-sample form of `x̄ ± z·SE`; the wider `t` multiplier pays for estimating `s` from the same data. It assumes the sampling distribution of the *mean* is approximately normal — the Central Limit Theorem delivers this for these metrics at screen sample sizes — and finite variance. It is deterministic and fast, which is why it is preferred over a bootstrap CI. The critical value `t(df, c)` comes from a small hard-coded two-sided table for `df` 1–30 and falls back to the normal `z` (the same `Z_CLEAR` / `Z_LEAN` as above) for `df ≥ 31`, where `t ≈ z`. With `n ≤ 1` the spread is unknown and the interval is all of `(−∞, +∞)`, the continuous mirror of the Wilson `(0, 1)` at `n = 0`.

**Continuous verdict** (`stats.mean_verdict`) mirrors the proportion verdict tier-for-tier against a baseline mean (the global or, like tilt, the account's own average): below `VERDICT_FLOOR` games → *not enough data*; the 95% t-interval excluding the baseline → *clear*; only the looser 80% band excluding it → *leaning*; otherwise *not enough data*. "strength" just means the personal mean sits above the baseline and "weakness" below — a value-neutral direction, since higher is good for net worth but bad for deaths. That polarity is a presentation choice, not a statistics one, so it lives in the assembly layer (`api.service`, which flips the tier for a "lower is better" metric like deaths), not here — keeping one tested verdict function that never learns which way a metric points, and the frontend rendering only.

**Continuous-metric baselines** are computed live, not stored. The `baseline_*` snapshot tables carry only win/loss counts and item purchase timing (the Analytics API exposes no per-hero mean net worth, damage, etc.), so the "global" mean for a continuous metric is taken straight from `match_players`: every ingested player's per-match value, `AVG`-ed over the *same* era/badge/game-mode predicates as the personal side, with each population row badge-scoped by its own team average. The scoped account is excluded (`account_id != self`), so the comparison is "you vs the field" — the live analogue of the snapshot baselines being external to you. The per-hero baseline is that hero's population mean; the **overall** baseline pools only the heroes you actually played (`hero_id IN (your heroes)`), mirroring how `matchups()` re-sums its overall baseline over exactly your played pairs so hero mix can't skew the comparison. A metric that is NULL across the whole population (e.g. a hero only you have played, or a column the API never filled) has no baseline and is shown personal-only — `mean_verdict` is never invoked against nothing. Unlike the snapshot baselines, this one needs no extra ingestion; it is a read over data already stored. The **death-timeline baseline** (the Deaths screen's "when you die" view) works the same way: the field's deaths-per-game in each game-minute is `kill_events` deaths over `match_players` games at the scope, the scoped account again excluded, so a minute below the field reads as a strength through the same flipped-`deaths` verdict — no stored death baseline, no extra fetch.

There is deliberately **no shrinkage for means**. A principled normal-normal pull toward the baseline needs a prior-variance knob that is arbitrary and different per metric — complexity without clear benefit. And the one simple pull that would mirror the Beta shrinkage, `(n·x̄ + k·μ) / (n + k)`, is just a weighted average of `x̄` and the baseline `μ`, so it always lands on `x̄`'s side of `μ`; the "shrinkage agrees on direction" guard that earns the proportion verdict its *leaning* tier would be vacuous here and change no verdict. Shrinkage stays with win rate.

### Session / tilt analysis

A separate, time-aware slice (the Tilt screen). The API exposes no session id, so a **session** is inferred from the gaps between an account's consecutive matches: a gap of `SESSION_GAP_S` seconds or more starts a new session. `SESSION_GAP_S = 3 hours` is the one knob, defined in `stats/sessions.py` (a pure module, like the rest of `stats/`).

From the time-ordered matches we compute two bucketings, both pure counting:

- **By game-number-within-session.** Position 1 is the first game of a sitting, 2 the second, …; positions at or past a cap (6) fold into a `6+` tail bucket.
- **By preceding-loss-streak.** For each game, how many consecutive losses came immediately before it *within the same session* (0 = fresh or right after a win). The counter resets on any win **and at every session boundary** — tilt is modelled as something that builds during a sitting and clears after a break. Streaks at or past a cap (3) fold into a `3+` tail bucket.

Each bucket's `(wins, games)` is then run through the *same* Wilson/shrinkage/verdict machinery above, with one difference in the reference rate: the baseline is the account's **own overall in-scope win rate**, not the global population. The question is "do you play worse than your usual self when deep in a session / on a losing streak?", so you are your own baseline. Because each bucket is a subset of that overall rate, the comparison is mildly conservative — it will not overstate tilt. Thin buckets fall under the verdict floor (5 games) and read as *not enough data*, exactly like every other screen.

### Recurring players

Because `match_players` stores all 12 players of every match, the other real players you keep meeting are already in the database — no party id, no re-fetching. The Recurring players screen surfaces them. The query is the **self-join twin of the hero matchup**: join `match_players` to itself on the same `match_id`, but key on the *other* player's `account_id` (not their hero) and split by `other.team = me.team` — same team means a **teammate**, opposite means an **opponent**. Per shared player we count `games = COUNT(*)` and `wins = SUM(me.won)` (`me.won` is identical across a match, so this is the shared games *you* won — with that teammate, or against that opponent).

Two thresholds, deliberately different numbers:

- **Co-occurrence gate.** A player must share at least `MIN_CO_OCCURRENCE = 3` of your matches to be listed at all. It lives in `stats/recurring.py` (a pure module like `stats/sessions.py`), which also splits the rows into teammates/opponents and sorts each most-shared first.
- **Verdict floor.** The usual 5-game floor still governs whether a listed player earns a verdict. Since the gate (3) is below the floor (5), a player you've shared 3–4 games with *appears* but reads *not enough data* — the honesty contract working, not a row hidden.

Each survivor's `(wins, games)` runs through the same Wilson/shrinkage/verdict machinery, baselined — like tilt — against the account's **own win rate over the same match set**: its overall in-scope rate, or, when the "my hero" filter is active, its rate *on that hero* (the co-occurrence counts are hero-filtered to match, so baseline and subject stay comparable). Names exist only for tracked accounts; every other player is surfaced by `account_id`, with display names left to a later source.

### Laning stats (early-game snapshot)

Backs the Laning screen. The per-player `stats[]` time series in `raw_json` carries the cumulative state every ~180 s (docs/api-findings.md, "Per-player `stats[]` time series"). For the early game we only need one point — the end of laning — so `laning_stats(match_id, player_slot, net_worth, last_hits, denies, sampled_at_s)` materializes the snapshot **`stats.laning.laning_mark`** picks: the latest snapshot at or before **`LANE_END_S = 600`** (so ~540 s in a normal match). `sampled_at_s` records which snapshot, so reads are honest about the mark.

Three deliberate choices, all mirroring `kill_events`:

- **Derived, not denormalized.** A row carries only the three snapshot metrics; hero / account / team / lane come from joining `match_players` on `(match_id, player_slot)` at read time, so `match_players` stays the single source of truth and can't drift.
- **`last_hits` is the snapshot's `creep_kills`.** The per-snapshot `last_hits` field is null in the payload; `creep_kills` is the cumulative last-hit proxy.
- **No mark, no row.** A match that never reached the lane-end mark yields no row (NULL, never a fabricated 0), so a short game can't poison the population baseline. The baseline itself is the **live population mean** at the mark, computed like the Performance baseline (there is no stored continuous baseline).

Like `kill_events`, it derives during ingest and backfills historical matches from the archive via `reprocess-archive` with zero API calls.

## What's deliberately not here yet

Death timestamps and positions, full soul curves over time (we store only the lane-end point today; see "Laning stats" for that slice), and ability builds. All of them slot in as new tables keyed on `(match_id, player_slot)` without touching anything above, and `raw_json` means some can be backfilled without re-fetching. That's the test the schema needed to pass.
