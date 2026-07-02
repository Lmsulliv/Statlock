# Deadlock Stat Tracker: Analysis & Presentation Layer

Third and final design doc, building on the data model and the ingestion worker spec. This one describes what users actually see: the screens, the queries behind them, the API contracts between backend and frontend, and the rules for presenting statistics honestly.

## Architecture shape

Three layers, with a strict one-way flow:

```
[ingestion worker] --writes--> [SQLite] <--reads-- [backend API] <--JSON-- [frontend]
```

- The **worker** (already specced) is the only thing that writes match data.
- The **backend API** is a thin web server exposing read-only JSON endpoints. It owns all SQL and all statistics math (Wilson intervals, shrinkage). Suggested stack: FastAPI (Python), since the worker will share its codebase and its statistical helpers.
- The **frontend** is a single-page app (React + Vite is the default suggestion, and matches your existing fantasy analyzer experience) that renders tables and charts. It contains zero statistics logic; it displays what the API computed.

Deployment path, restated from our discussion: everything runs on `localhost` first, then the identical stack moves to a small VPS when friends join. The only change at that point is adding simple authentication and pointing a domain at it.

### CLI stepping stone

Before any frontend exists, ship a `stats` command that prints the matchup table to the terminal using the same backend functions. This gets the statistics layer testable in week one and guarantees the logic never lives in UI code.

## The scope selector (global UI state)

Every analytical screen shares one control cluster, and every API call carries its values:

| Control | Options | Default |
|---|---|---|
| Account | any tracked account | your `is_self` account |
| Era | current era / pick several / all time | current era |
| Rank range | full slider from lowest to highest badge | all ranks at first; tighten once you have volume |
| Min. games | threshold for showing a row at all | 3 |

Two rules:

1. Scope is encoded in the URL query string, so any view can be bookmarked or sent to a friend and renders identically.
2. The active scope is always visibly labeled on screen ("Current era, all ranks, 134 games"), because a stat without its scope printed next to it is how misreadings happen.

## Screens

### 1. Overview (home)

The "is everything alive and how am I doing" page.

- Rank/MMR graph over time (from the MMR endpoint data).
- Last 10 matches: hero, result, KDA, souls, link to match detail later.
- Sync status badge straight from `fetch_queue` counts: queue depth, last discovery time, and an `unavailable` count, which doubles as your "how many old reports still need unlocking" meter.
- If an era candidate is pending confirmation, the banner lives here.

### 2. Matchups

The core screen. One row per enemy hero (optionally filtered to a specific hero you play):

| Column | Source |
|---|---|
| Enemy hero | `v_my_matchups` |
| Games / Wins | personal counts |
| Win rate + 95% CI | Wilson interval, rendered as a bar with whiskers, never a bare number |
| Global baseline | summed baseline counts for the selected scope |
| Adjusted delta | shrinkage estimate minus global |
| Verdict | badge: **Strength** / **Weakness** / *Not enough data* |

Verdict logic: a row earns Strength or Weakness only when the Wilson interval excludes the global rate. Everything else says Not enough data, in neutral gray, no matter how lopsided the raw percentage looks. Sorting defaults to |delta| among significant rows.

### 3. Items (per hero)

Same table pattern, one row per item for a chosen hero:

- Personal games/wins with the item, Wilson CI, global baseline, adjusted delta, verdict.
- One extra column: **purchase timing delta**, your average purchase time minus the global average (`avg_purchase_s` columns). "You buy this 3:40 later than average" is independent of win rate and often the more actionable number.

### 4. Directions for improvement

The screen the whole project exists for. Not a table of everything, but a short ranked digest across both matchups and items:

- **Confirmed weaknesses**: significant negative deltas, largest first.
- **Confirmed strengths**: significant positive deltas (knowing what to lean on is coaching too).
- **Watch list**: large raw deltas whose intervals don't yet exclude the global rate, shown with "n games, need more data" so it's clear why they're not confirmed.

Each entry renders as a sentence, not a row: "Against Haze (12 games) you win 25% vs. a global 51% [CI 9–53%]. Confirmed weakness." This is the screen to show friends first.

### 5. Tilt

A time-aware view of *when* you play well, not *against whom*. Two stacked tables for the scoped account, each built from play sessions inferred from match-time gaps (see data-model.md, "Session / tilt analysis"):

- **By game number in session** — one row per session position (`1`, `2`, …, `6+`).
- **By loss streak** — one row per count of consecutive losses immediately before the game, within the session (`0 losses`, `1 loss`, `2 losses`, `3+ losses`).

Both tables reuse the matchups columns verbatim — sample size, Wilson interval bar, an "Your overall" baseline column, adjusted delta, verdict — but the baseline each bucket is judged against is **your own overall win rate in scope**, not a global population rate (the screen says so). Thin buckets read *not enough data* under the usual floor. A blurb states the session-gap constant and the session/overall game counts so the numbers are never shown without their scope. Rows render in their natural order (the progression is the signal), so unlike the other tables this one isn't re-sortable.

### 6. Recurring players

The other real players who keep sharing your matches (data-model.md, "Recurring players"). Two stacked tables for the scoped account:

- **Teammates** — your win rate *with* each player who recurs on your team.
- **Opponents** — your win rate *against* each player who recurs on the enemy team.

Both reuse the matchups columns minus the hero icon — sample size, Wilson interval bar, a "Your overall" baseline column, adjusted delta, verdict — and, like Tilt, the baseline each player is judged against is **your own win rate over the same matches** (overall, or on the selected hero when the "my hero" filter is set), not a global rate. A player is listed only once you've shared at least `MIN_CO_OCCURRENCE = 3` games; thinner co-occurrences are left off, and listed players under the 5-game verdict floor read *not enough data*. Other players are shown minimally — a tracked account's name, otherwise just `Account <id>` (names are a later source). Rows render most-shared first and aren't re-sortable. A blurb prints the baseline and the co-occurrence threshold so the numbers are never shown without their scope.

### 7. Performance (continuous metrics)

Per-hero and overall continuous-metric performance — the "what am I actually doing in the game" companion to win rate. One block per scope row (overall first, then each hero A→Z), each a small table with one row per metric: **net worth per minute** (`net_worth` over `duration_s`), kills, deaths, assists, last hits, denies, player damage, obj damage, healing, and **damage taken** (net, post-mitigation total from the last `stats[]` entry; like deaths, lower is better). Net worth is per-minute; the rest are per-game averages.

Each row reuses the matchups vocabulary — sample size, an interval bar (here a mean with its 95% **t**-interval, drawn on the metric's own scale rather than as a percentage), a baseline column, a raw delta in metric units, and a verdict. The baseline is the **live population mean** for that metric at the same scope — every other player's ingested games, the owner excluded — since there is no stored continuous baseline (data-model.md, "Continuous-metric baselines"). The overall row's baseline is restricted to exactly the heroes you played, mirroring the matchups overall baseline.

Two honesty notes specific to this screen:

- **Verdict is good/bad, not above/below.** For a metric where lower is better (deaths), beating the field reads as a *strength*. The statistics layer stays value-neutral; the assembly layer flips the tier (data-model.md), so the frontend still just renders the verdict it's given.
- **No baseline, no comparison.** A metric nobody else has data for — a hero only you have played, or an all-NULL column — shows personal-only and reads *not enough data* rather than comparing against nothing.

### 8. Laning (early game)

Lane outcomes drive Deadlock games, so this screen reports the early game directly: **net worth, last hits, and denies at the lane-end mark** (~10 minutes, `stats.laning.LANE_END_S`), per hero and overall, each against the live population at the same mark. It is the Performance screen's early-game sibling and shares its layout exactly — one block per scope row (overall first, then heroes A→Z), one metric per row, with sample size, a 95% **t**-interval bar, a baseline column, a raw delta, and a verdict — reusing the same components and the same assembly path (`api.service._continuous_rows`), so the two can't drift.

Two differences from Performance:

- **Read at a fixed time, not per minute.** Every player's snapshot is taken at the same lane-end mark, so the values are raw cumulative numbers (net worth, last hits, denies), directly comparable without normalizing by duration. The values come from the per-player `stats[]` time series, materialized once into `laning_stats` (data-model.md, "Laning stats") — last hits is the snapshot's `creep_kills`, since the per-snapshot `last_hits` field is null (docs/api-findings.md).
- **No laning snapshot, no row.** A match that ended before laning closed has no lane-end snapshot and simply drops out (NULL, never a fabricated 0), so it can't distort your mean or the baseline.

### 8b. Deaths (coaching)

Aggregates the per-kill `kill_events` table (and the `damage_taken_sources` table) across the scoped match set into coaching views — the cross-game companion to the per-match kill trades the match-detail view already shows. All attribute via `match_players` on `(match_id, player_slot)`, so an anonymized opponent (`account_id = 0`) still counts under the hero it piloted.

- **Who kills you.** A ranking of enemy heroes by how often they were the one that killed you, with the games you faced each for context. These are **raw counts with no verdict** — there is no stored per-matchup death baseline, so, exactly like the match-detail trades, the screen never fabricates one. Deaths off fewer than `VERDICT_FLOOR` games faced are muted (a count off one game means little). Tower/creep deaths (NULL killer) belong to no hero and are excluded from this ranking.
- **Who damages you.** Beside the kill ranking, the enemy heroes that deal you the most damage, as **average gross damage per game** you faced them (from `damage_matrix`, materialized into `damage_taken_sources`; data-model.md, "Damage taken"). Also **raw, no verdict and no baseline** — this is pre-mitigation damage that doesn't reconcile with the net damage-taken total, so it's an honest *relative* ranking only. Environment/non-roster damage (NULL source) and your own damage are excluded, the same way the kill ranking drops tower/creep deaths.
- **When you die.** Your deaths bucketed into game-minute bins (`stats.deaths`, the long tail folded into a trailing `30m+` bin), shown as a small bar chart of **deaths per game** in each minute against the **live population baseline** for that minute (everyone else at this scope, computed straight from `kill_events` — see data-model.md, "Continuous-metric baselines"). Fewer deaths is better, so the assembly layer flips the verdict tier (like the `deaths` metric on Performance): a minute clearly below the field reads as a *strength*, clearly above as a *weakness*. A minute with no population to compare against stays neutral (*not enough data*). Untimed deaths can't be placed on the timeline and are dropped from it (they still count in the by-hero ranking).

### 9. Era manager (admin)

Small page backing the patch-notes detection discussed below:

- List of eras with start dates and labels; edit and redraw boundaries (matches keep exact `patch_id`s, so re-binning is always safe).
- Pending era candidates with a link to the source patch notes post, change-line count, and Confirm / Dismiss buttons.
- Confirming a candidate also closes the previous era at the new start date and triggers a baseline fetch for the new era's date range, so the new era has global numbers from day one.

> **Interim owner gate (not authentication).** There is no login yet, so this admin page — the app's only write surface — is gated by a deploy-time config flag, *not* real auth. The frontend hides the nav link and `/eras` route unless `VITE_OWNER=true`; the API returns **403** on the confirm/dismiss POSTs unless `DEADLOCK_OWNER` is set. The 403 is the real enforcement (anyone can call the API directly); the hidden nav is convenience. Replace both with a real login before exposing the app publicly.

## Patch-notes-assisted era detection

Add to the nightly maintenance loop:

```
poll Steam News API (GetNewsForApp, Deadlock app id) for new posts
for each unseen post:
    score = f(title keywords, change-line count, hero-name mentions)
    if score > threshold:
        INSERT INTO era_candidates (post_url, posted_at, score, status='pending')
```

```sql
CREATE TABLE era_candidates (
    candidate_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    post_url      TEXT NOT NULL,
    post_title    TEXT,
    posted_at     TEXT NOT NULL,
    change_lines  INTEGER,
    score         REAL,
    status        TEXT NOT NULL DEFAULT 'pending'  -- pending | confirmed | dismissed
);
```

Design stance: **the system proposes, you decide.** Confirming a candidate creates the `patch_eras` row with one click; dismissing it leaves the current era running. A heuristic can count change lines, but it cannot know that a single urn rework line outweighs forty number tweaks. Start the threshold loose (flag generously, dismiss freely) and tighten it once you've seen a month of candidates.

## API contract sketch

All endpoints are GET, all take the scope params (`account_id`, `era_ids`, `badge_min`, `badge_max`, `min_games`).

```
GET /api/overview
GET /api/matchups?hero_id=optional
GET /api/items?hero_id=required
GET /api/performance            (continuous metrics per hero + overall, population-baselined)
GET /api/laning                 (net worth / last hits / denies at the lane-end mark, population-baselined)
GET /api/death-patterns         (deaths by enemy hero [raw], + death timing per game-minute vs live population)
GET /api/improvement
GET /api/recurring-players?hero_id=optional   (teammates + opponents, self-baselined)
GET /api/eras                  (+ POST confirm/dismiss for candidates)
GET /api/sync-status
GET /api/accounts              (scope-free; backs the Account picker — account_id, display_name, is_self)
```

Example response row for /api/matchups:

```json
{
  "enemy_hero_id": 17,
  "enemy_hero_name": "Haze",
  "games": 12,
  "wins": 3,
  "winrate": 0.25,
  "ci_low": 0.089,
  "ci_high": 0.532,
  "global_rate": 0.514,
  "global_matches": 48211,
  "adjusted_rate": 0.368,
  "delta": -0.146,
  "verdict": "weakness"
}
```

Note the response carries everything the UI needs pre-computed, including the verdict. The frontend never recomputes statistics, which keeps the math in exactly one tested place.

## Presentation rules (the honesty contract)

1. **No bare percentages.** Every personal rate renders with its interval. Small samples get visibly huge whiskers, and that's the feature working.
2. **Color means significance, not magnitude.** A 20-point delta on 4 games stays gray; a 6-point delta on 80 games can be red. Color is reserved for "the interval excludes the baseline."
3. **Sample sizes are always visible**, on personal rows and on the global baselines alike.
4. **Scope is always printed** next to any number it produced.
5. **Empty states explain themselves.** A new user sees "3 matches ingested, 47 queued, come back in an hour," not a blank table.
6. Baseline-backed fields are always era-scoped server-side via explicit date ranges (see ingestion spec, Loop 3); "all time" is a deliberately wide explicit range, never an omitted parameter.

## Verify-before-building list

Carried over from earlier discussion, the things the first hour of implementation should confirm against the live API before trusting this spec:

1. Which nullable `match_players` columns the metadata endpoint actually populates (lane, denies, purchase timestamps).
2. Whether analytics endpoints accept date-range or version filters that map onto eras, or only fixed windows (if fixed, eras approximate via date ranges).
3. The finest rank-bracket granularity the analytics endpoints return.
4. Steam News API output for a known major patch vs. a known minor one, to calibrate the era-candidate scoring.

## Acceptance scenarios

1. A matchup with 2 games never displays a verdict, regardless of record.
2. Changing the rank-range slider changes both personal stats and the global baseline consistently (baseline re-sums counts across the included brackets).
3. Redrawing an era boundary recomputes every era-scoped stat correctly with no re-ingestion.
4. A bookmarked URL with scope params renders the identical view on another machine.
5. The improvement screen never shows an unconfirmed delta outside the watch list.
6. With the database empty, every screen renders a helpful empty state, not an error.
7. On Recurring players, a co-player you've shared only 2 games with is never listed; one you've shared 3–4 with is listed but shows no verdict (only ≥5 can), and an untracked player appears as `Account <id>`.
