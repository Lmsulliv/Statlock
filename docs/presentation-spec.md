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

### 5. Era manager (admin)

Small page backing the patch-notes detection discussed below:

- List of eras with start dates and labels; edit and redraw boundaries (matches keep exact `patch_id`s, so re-binning is always safe).
- Pending era candidates with a link to the source patch notes post, change-line count, and Confirm / Dismiss buttons.
- Confirming a candidate also closes the previous era at the new start date and triggers a baseline fetch for the new era's date range, so the new era has global numbers from day one.

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
GET /api/improvement
GET /api/eras                  (+ POST confirm/dismiss for candidates)
GET /api/sync-status
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
