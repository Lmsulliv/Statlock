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

## Navigation: five tabs and a management gear

The app shows five nav tabs — **Overview, Heroes, Performance, Insights, Improvement** — so a first-time visitor can navigate cold. Management (the Accounts importer and the Era manager) lives behind a gear icon in the header, shown only to viewers who can manage (see "Management" below). Match detail stays a drill-in route reached from the recent-matches list.

Sub-views are addressed by URL **path** (`/performance/laning`, `/insights/deaths`, `/heroes/all`), never by query param: the scope bar rewrites the query string from scope values alone, so a `?tab=` parameter would be silently erased by the first scope change. Nav and sub-view links always carry the current query string, so the active scope follows every navigation (scope rule 1).

**Legacy routes.** Every route from the earlier ten-tab layout redirects into the new structure with the query string preserved, so old bookmarks and shared links keep rendering the same data:

| Old route | Redirects to |
|---|---|
| `/matchups` | `/heroes/all` — the aggregate matchup grid; `hero_id` still acts as the "my hero" filter |
| `/items` | `/heroes` — the hero detail (its Items section); the hero comes from `hero_id`, else the most-played default |
| `/laning` | `/performance/laning` |
| `/trends` | `/performance/trends` |
| `/deaths` | `/insights/deaths` |
| `/tilt` | `/insights/sessions` |
| `/recurring-players` | `/insights/players` |

`/performance` and `/improvement` kept their paths.

## Public profiles & sharing

Reads are open by design (the honesty contract below applies to every viewer, not just the owner), so any account the app holds data for is shareable at **`/player/:accountId`** — the same five tabs, scoped to that account and rendered read-only. On this route:

- The account is **pinned by the URL path**, not the scope query param: the scope bar's Account switcher is hidden and the pinned id can't be edited away. Every other scope filter (era, rank range, hero, mode, min games) stays usable, and in-app links are prefixed with `/player/:accountId` so navigation stays on the profile.
- **All management and write UI is hidden** regardless of login state — the gear menu, the era-candidate banner, inline rename, the account importer, and the login/logout controls. (The API still enforces every write; hiding the UI is presentation.) The onboarding progress card is also skipped, since its poll nudges the worker and an anonymous visitor shouldn't trigger that.
- A small **profile header** shows the account's public display name (Steam persona or bare id — never anyone's private label) and current rank badge, standing in for the hidden account switcher.
- An account we hold **no data** for (or a malformed id) gets a friendly "this account isn't tracked here" page naming what tracking would show, not empty screens.

**Demo profile.** When `DEMO_ACCOUNT_ID` is configured, the logged-out landing (the empty Overview) shows a prominent "View a live demo profile" button linking to that account's `/player/:id` — the primary path for a cold visitor who won't sign in. It is surfaced to the frontend on `/api/auth/me` (`demo_account_id`) and self-hides when unset. In auth mode the same landing points a signed-out visitor at Steam login rather than the server-side CLI commands they can't run.

**Link previews.** The API injects Open Graph / Twitter card tags into the SPA shell per request (`api/meta.py`), so a pasted link unfurls with a real title. A `/player/:id` link is personalized with the resolved public name and rank via cheap indexed lookups; every other path — and any lookup that misses or errors — falls back to generic site tags. `og:url` comes from `DEADLOCK_BASE_URL` and is omitted when unset.

> Note: this section documents behavior added after the original five-tab spec; the `/player/:accountId` route, the `/api/players/{id}/profile` endpoint, server-side OG injection, and `DEMO_ACCOUNT_ID` were not in the earlier draft.

## Screens

### 1. Overview (home)

The landing page: "how am I doing, and where should I look next."

- Rank/MMR graph over time (from the MMR endpoint data), with the current badge beside the title.
- **Focus areas** — the 30-second hook: the top three entries of the improvement digest (`/api/improvement`; confirmed weaknesses first, then the watch list — both arrive server-ranked largest-first, so the strip is a slice, never a re-ranking), each a compact card with its verdict, linking into Improvement. Hidden when the digest is empty and under Street Brawl (the digest is Normal-only).
- **Your heroes** — the most-played heroes as compact cards (games, win rate + interval, verdict color, per-row provisional flag) from `/api/hero-records`, sorted by games client-side (presentation ordering), each linking into that hero's detail on the Heroes tab. Shares a two-column row with the rank chart on wide screens.
- Last 10 matches: hero, result, KDA, souls, link to match detail.
- **Sync status is no longer a card here.** It is a small header indicator — a dot (amber while the queue is non-empty, an operational state, not a verdict) plus a compact count — whose tooltip carries the full old card text: fetched/queued/unavailable counts and the last discovery/maintenance times. The `unavailable` count still doubles as the "how many old reports still need unlocking" meter. The no-account empty state keeps the full sync detail inline, where it matters most.
- If an era candidate is pending confirmation, the banner lives here — **shown only to viewers who can manage**, since it links into the Era manager, which nobody else can reach.

### 2. Heroes

Merges the old Matchups and Items tabs into one hero-centric screen: a played-heroes list beside a per-hero detail. The list comes from `/api/hero-records` — one row per hero with games, win rate + Wilson interval, and a verdict against the account's **own overall in-scope rate** (the tilt/recurring self-baseline), plus a per-row provisional flag. Rows order most-played first.

**Selection.** The selected hero is the scope's `hero_id` — the same parameter the scope bar's "My hero" control writes, so the list and the control can never disagree. With no hero selected the detail opens on the **most-played hero** (resolved in memory from the hero-records rows; the URL only changes when the user picks), so a user with data never sees an empty picker. An **"All heroes"** entry (`/heroes/all`) renders the aggregate matchup grid instead of a detail; there the path is authoritative — changing "My hero" filters the grid exactly as the old Matchups screen did, without leaving the aggregate view.

The detail view has four sections, each reusing the endpoint its old standalone screen used, narrowed to the hero — matchups and items via `hero_id`, laning and performance by keeping only that hero's row of the account-wide response:

#### Matchup table (the aggregate grid and the per-hero section)

One row per enemy hero:

| Column | Source |
|---|---|
| Enemy hero | `v_my_matchups` |
| Games / Wins | personal counts |
| Win rate + 95% CI | Wilson interval, rendered as a bar with whiskers, never a bare number |
| Global baseline | summed baseline counts for the selected scope |
| Adjusted delta | shrinkage estimate minus global |
| Verdict | badge: **Strength** / **Weakness** / *Not enough data* |

Verdict logic: a row earns Strength or Weakness only when the Wilson interval excludes the global rate. Everything else says Not enough data, in neutral gray, no matter how lopsided the raw percentage looks. Sorting defaults to |delta| among significant rows.

#### Item table (per hero)

Same table pattern, one row per item for the selected hero:

- Personal games/wins with the item, Wilson CI, global baseline, adjusted delta, verdict.
- One extra column: **purchase timing delta**, your average purchase time minus the global average (`avg_purchase_s` columns). "You buy this 3:40 later than average" is independent of win rate and often the more actionable number.

Items are inherently per-hero (`hero_id` is required by the endpoint); the most-played default means the section always has a concrete hero, so the old "pick a hero" prompt no longer exists.

#### Skill order (per hero)

Your own skill-up habits on the selected hero, from `/api/hero-skill-order?hero_id=`: the most common **opening sequence** (first four points) and the ability you **max first**, each with the game count it rests on, then the same two facts split by **wins vs losses**. **Descriptive, not a verdict** — there is no population baseline for skill order, so this is a record of *what you did*, presented as raw sequences and counts with no Wilson interval or verdict color (an InfoTip says so). The wins/losses split appears only once **each** side clears the sample-size floor (`VERDICT_FLOOR`); below it, a note explains why. Empty state when the hero has no ability data (old or summary-only matches). Underlying rows come from `ability_events` (see data-model.md); the aggregation is pure `stats.ability_order`.

#### Laning and Performance sections

The hero's row of `/api/laning` and `/api/performance`, rendered with the same metric-table component the Performance tab uses (see below), so a hero's early game and per-game numbers sit beside its matchups and items.

### 3. Performance

One tab with a three-way segment control sharing one scope bar: **Overall**, **Laning**, and **Over time**. The segments are paths (`/performance`, `/performance/laning`, `/performance/trends`) — see Navigation. Overall and Laning are Normal-only; Over time renders under Street Brawl too, each gate applying per segment.

#### Overall (continuous metrics)

Per-hero and overall continuous-metric performance — the "what am I actually doing in the game" companion to win rate. One block per scope row (overall first, then each hero A→Z), each a small table with one row per metric: **net worth per minute** (`net_worth` over `duration_s`), kills, deaths, assists, last hits, denies, player damage, obj damage, healing, and **damage taken** (net, post-mitigation total from the last `stats[]` entry; like deaths, lower is better). Net worth is per-minute; the rest are per-game averages.

Each row reuses the matchups vocabulary — sample size, an interval bar (here a mean with its 95% **t**-interval, drawn on the metric's own scale rather than as a percentage), a baseline column, a raw delta in metric units, and a verdict. The baseline is the **live population mean** for that metric at the same scope — every other player's ingested games, the owner excluded — since there is no stored continuous baseline (data-model.md, "Continuous-metric baselines"). The overall row's baseline is restricted to exactly the heroes you played, mirroring the matchups overall baseline.

Two honesty notes specific to this view:

- **Verdict is good/bad, not above/below.** For a metric where lower is better (deaths), beating the field reads as a *strength*. The statistics layer stays value-neutral; the assembly layer flips the tier (data-model.md), so the frontend still just renders the verdict it's given.
- **No baseline, no comparison.** A metric nobody else has data for — a hero only you have played, or an all-NULL column — shows personal-only and reads *not enough data* rather than comparing against nothing.

#### Laning (early game)

Lane outcomes drive Deadlock games, so this view reports the early game directly: **net worth, last hits, and denies at the lane-end mark** (~10 minutes, `stats.laning.LANE_END_S`), per hero and overall, each against the live population at the same mark. It is the Overall view's early-game sibling and shares its layout exactly — one block per scope row (overall first, then heroes A→Z), one metric per row, with sample size, a 95% **t**-interval bar, a baseline column, a raw delta, and a verdict — reusing the same components and the same assembly path (`api.service._continuous_rows`), so the two can't drift.

Two differences from the Overall view:

- **Read at a fixed time, not per minute.** Every player's snapshot is taken at the same lane-end mark, so the values are raw cumulative numbers (net worth, last hits, denies), directly comparable without normalizing by duration. The values come from the per-player `stats[]` time series, materialized once into `laning_stats` (data-model.md, "Laning stats") — last hits is the snapshot's `creep_kills`, since the per-snapshot `last_hits` field is null (docs/api-findings.md).
- **No laning snapshot, no row.** A match that ended before laning closed has no lane-end snapshot and simply drops out (NULL, never a fabricated 0), so it can't distort your mean or the baseline.

#### Over time (trends)

Win rate and the same continuous metrics as a chronological series (`/api/trends`) — "am I getting better," the project's namesake question. Two view modes, toggled in the UI: a **rolling average** over the last N games (window size adjustable, default 20) or **calendar buckets** (week or month). The toggles are view state, not scope — they live in component state, deliberately not in the URL, which the scope bar owns.

Each metric renders as a sparkline against a single gold reference line: the account's overall in-scope win rate for `win_rate`, the live population mean for a continuous metric (absent when there is no baseline). The honesty floor carries into time: a window or bucket under `VERDICT_FLOOR` games reads *not enough data* — a greyed hollow dot that breaks the trend line — so a lucky 3-game week never looks like a real swing. The response carries the `provisional` flag (see below).

### 4. Insights

The coaching analyses that aren't hero- or metric-shaped — **Deaths**, the session analysis (titled **"Do you tilt?"**), and **Recurring players** — as distinct cards on one screen, each deep-linkable (`/insights/deaths`, `/insights/sessions`, `/insights/players`; landing on a section scrolls it into view). Under Street Brawl, Deaths and Recurring players show a compact Normal-only note inside their card while "Do you tilt?" keeps rendering — the gate is per-section, so muting two analyses never silences the third.

#### Deaths (coaching)

Aggregates the per-kill `kill_events` table (and the `damage_taken_sources` table) across the scoped match set into coaching views — the cross-game companion to the per-match kill trades the match-detail view already shows. All attribute via `match_players` on `(match_id, player_slot)`, so an anonymized opponent (`account_id = 0`) still counts under the hero it piloted. The two rankings render side by side (they're both raw, no-verdict counts); the timing chart keeps the full width beneath them.

- **Who kills you.** A ranking of enemy heroes by how often they were the one that killed you, with the games you faced each for context. These are **raw counts with no verdict** — there is no stored per-matchup death baseline, so, exactly like the match-detail trades, the screen never fabricates one. Deaths off fewer than `VERDICT_FLOOR` games faced are muted (a count off one game means little). Tower/creep deaths (NULL killer) belong to no hero and are excluded from this ranking.
- **Who damages you.** Beside the kill ranking, the enemy heroes that deal you the most damage, as **average gross damage per game** you faced them (from `damage_matrix`, materialized into `damage_taken_sources`; data-model.md, "Damage taken"). Also **raw, no verdict and no baseline** — this is pre-mitigation damage that doesn't reconcile with the net damage-taken total, so it's an honest *relative* ranking only. Environment/non-roster damage (NULL source) and your own damage are excluded, the same way the kill ranking drops tower/creep deaths.
- **When you die.** Your deaths bucketed into game-minute bins (`stats.deaths`, the long tail folded into a trailing `30m+` bin), shown as a small bar chart of **deaths per game** in each minute against the **live population baseline** for that minute (everyone else at this scope, computed straight from `kill_events` — see data-model.md, "Continuous-metric baselines"). Fewer deaths is better, so the assembly layer flips the verdict tier (like the `deaths` metric on Performance): a minute clearly below the field reads as a *strength*, clearly above as a *weakness*. A minute with no population to compare against stays neutral (*not enough data*). Untimed deaths can't be placed on the timeline and are dropped from it (they still count in the by-hero ranking).

#### Do you tilt? (sessions)

A time-aware view of *when* you play well, not *against whom*. Two tables side by side for the scoped account, each built from play sessions inferred from match-time gaps (see data-model.md, "Session / tilt analysis"):

- **By game number in session** — one row per session position (`1`, `2`, …, `6+`).
- **By loss streak** — one row per count of consecutive losses immediately before the game, within the session (`0 losses`, `1 loss`, `2 losses`, `3+ losses`).

Both tables reuse the matchups columns verbatim — sample size, Wilson interval bar, a "Your overall" baseline column, adjusted delta, verdict — but the baseline each bucket is judged against is **your own overall win rate in scope**, not a global population rate (the screen says so). Thin buckets read *not enough data* under the usual floor. A blurb states the session-gap constant and the session/overall game counts so the numbers are never shown without their scope. Rows render in their natural order (the progression is the signal), so unlike the other tables this one isn't re-sortable.

#### Recurring players

The other real players who keep sharing your matches (data-model.md, "Recurring players"). Two tables side by side for the scoped account:

- **Teammates** — your win rate *with* each player who recurs on your team.
- **Opponents** — your win rate *against* each player who recurs on the enemy team.

Both reuse the matchups columns minus the hero icon — sample size, Wilson interval bar, a "Your overall" baseline column, adjusted delta, verdict — and, like the session analysis, the baseline each player is judged against is **your own win rate over the same matches** (overall, or on the selected hero when the "my hero" filter is set), not a global rate. A player is listed only once you've shared at least `MIN_CO_OCCURRENCE = 3` games; thinner co-occurrences are left off, and listed players under the 5-game verdict floor read *not enough data*. Other players are shown minimally — a tracked account's name, otherwise just `Account <id>` (names are a later source). Rows render most-shared first and aren't re-sortable. A blurb prints the baseline and the co-occurrence threshold so the numbers are never shown without their scope.

### 5. Directions for improvement

The screen the whole project exists for. Not a table of everything, but a short ranked digest across both matchups and items:

- **Confirmed weaknesses**: significant negative deltas, largest first.
- **Confirmed strengths**: significant positive deltas (knowing what to lean on is coaching too).
- **Watch list**: large raw deltas whose intervals don't yet exclude the global rate, shown with "n games, need more data" so it's clear why they're not confirmed.

Each entry renders as a sentence, not a row: "Against Haze (12 games) you win 25% vs. a global 51% [CI 9–53%]. Confirmed weakness." This is the screen to show friends first. The Overview's Focus areas strip renders the top three of these entries as its hook.

### 6. Management (behind the gear)

The header gear (visible only to viewers who can manage) opens a small menu with the two management surfaces; neither takes a nav tab:

- **Accounts** (`/accounts`): the account importer (adding a tracked account returns 202 and queues discovery; the onboarding progress card appears on success), a live ingestion-status panel polling `/api/sync-status`, and the tracked-accounts list with inline rename.
- **Era manager** (`/eras`), the small page backing the patch-notes detection discussed below:
  - List of eras with start dates and labels; edit and redraw boundaries (matches keep exact `patch_id`s, so re-binning is always safe).
  - Pending era candidates with a link to the source patch notes post, change-line count, and Confirm / Dismiss buttons.
  - Confirming a candidate also closes the previous era at the new start date and triggers a baseline fetch for the new era's date range, so the new era has global numbers from day one.

> **Who can manage.** With Steam login configured (`DEADLOCK_BASE_URL` set), the management surfaces — the gear, both routes, and the era banner — show for authenticated users. In local single-user mode (no login configured) the build-time `VITE_OWNER=true` flag shows them instead. Hiding the gear and not registering the routes is convenience only; the API enforces authentication (or the explicit local `DEADLOCK_OPEN_WRITES=1` escape hatch) on every write. This supersedes the earlier `DEADLOCK_OWNER` interim owner gate described in previous revisions of this spec.

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
GET /api/hero-skill-order?hero_id=required   (your opening sequence + first-maxed ability on a hero, wins/losses split; descriptive, no verdict)
GET /api/performance            (continuous metrics per hero + overall, population-baselined)
GET /api/laning                 (net worth / last hits / denies at the lane-end mark, population-baselined)
GET /api/death-patterns         (deaths by enemy hero [raw], + death timing per game-minute vs live population)
GET /api/trends?hero_id=optional (win rate + metrics over time; own mode/granularity/window_games params; provisional-aware)
GET /api/tilt                  (session-index + loss-streak buckets, self-baselined; provisional-aware)
GET /api/improvement
GET /api/recurring-players?hero_id=optional   (teammates + opponents, self-baselined)
GET /api/hero-records          (per-hero games/wins/Wilson/verdict vs own overall; provisional-aware; backs the Heroes list, its most-played default, and the Overview hero cards)
GET /api/heroes                (light played-hero picker — hero_id, name, image; backs the scope bar)
GET /api/ranks                 (scope-free rank-tier reference for the rank selector and MMR chart)
GET /api/matches/{match_id}    (match detail drill-in; optional account_id sets the "you" perspective)
GET /api/eras                  (+ POST confirm/dismiss for candidates)
GET /api/sync-status
GET /api/accounts              (scope-free; backs the Account picker — account_id, display_name, is_self)
GET /api/accounts/{id}/progress (scope-free; polled onboarding counts — known/analyzed/queued-by-tier)
GET /api/players/{id}/profile   (public, label-free profile header for /player/:id — display_name, current_rank, has_data; side-effect-free)
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

## Provisional summaries (instant onboarding)

A freshly imported account is useless until the rate-limited drain loop fetches full match metadata — hundreds of matches take hours. But discovery's match-history call already carries per-match hero, K/D/A, net worth, last hits, denies, result, and duration, which it materializes into `account_match_summaries` at import time (ingestion spec; schema v17). The presentation layer reads those summaries so summary-capable screens render within seconds and **upgrade in place** as metadata lands.

**The union view.** `v_account_matches` (schema v19) is one per-`(account_id, match_id)` row set over two sources:

- **`source = 'full'`** — `match_players ⋈ matches`, every column populated.
- **`source = 'summary'`** — an `account_match_summaries` row whose `(account_id, match_id)` has **no** `match_players` row yet. Its damage/healing (`player_damage`, `obj_damage`, `healing`, `player_damage_taken`), `era_id`, `team`, and badge columns are **NULL** — "we don't know", never zero.

The anti-join is the no-double-count guarantee: the instant a match's metadata is ingested (in ingestion's own transaction), its `summary` twin disappears and the `full` row replaces it. Every match is counted exactly once, and the upgrade is atomic.

**Which screens read it.** The personal reads that don't need enemy composition select from the view: Overview's last matches, Performance, Trends, Tilt, and the per-hero record (`/api/hero-records`). Matchups, Items, Laning, Deaths, and Recurring players are unchanged — they genuinely need full metadata (enemy roster, purchases, lane snapshots, kill events), which a summary doesn't carry, so they stay `full`-only and never go provisional.

**Two honesty rules fall out of the NULLs, not special-case code:**

- **NULL input → NULL metric.** A metric whose source column is NULL yields NULL for that row (e.g. a summary row contributes its kills but not its damage-taken); the stats layer already drops non-finite values, so the metric reads its own honest sample size. A summary row with an unknown result (`won IS NULL`) is dropped from any win-rate stream — it can neither win nor lose a bucket.
- **Narrowed scope drops summaries.** Era- and badge-narrowed scopes exclude summary rows automatically, because NULL fails both `era_id IN (…)` and the badge `BETWEEN`. At the default all-time, full-range scope (no era filter, badge predicate dropped) summaries are included. The population baseline on Performance/Trends is always `full`-only — a baseline must rest on real metadata, never another account's provisional guess.

**The `provisional` flag (contract).** Any response that can mix sources carries a top-level `"provisional": true|false`, true iff any contributing row is summary-backed, so the UI can badge the result "these numbers will improve" and re-poll. It appears on `/api/overview`, `/api/performance`, `/api/trends`, `/api/tilt`, and `/api/hero-records` (which also flags each hero row individually; Overview also carries per-row `source`). **Absent means always-full**: endpoints that never read summaries omit the flag entirely rather than hard-coding `false`. `/api/performance` returns `{"provisional": bool, "rows": [...]}` (the rows were a bare list before this contract).

**Progress endpoint.** `GET /api/accounts/{id}/progress` backs the onboarding UI's poll with cheap indexed counts: `known` (summary rows held), `analyzed` (matches with full metadata), and remaining queue depth by tier (`prioritized_pending`, `backfill_pending`, `deferred`, keyed on `fetch_queue.discovered_for_account`). As the drain loop works, `analyzed` climbs toward `known` and the pending tiers fall.

## Frontend presentation of onboarding and mode

These rules describe how the React app renders the contracts above. The frontend computes no statistics — it renders the counts and flags the API provides.

**Onboarding progress module.** After an import (Accounts screen) and on Overview whenever an account is still analyzing, a progress card polls `/api/accounts/{id}/progress` every 5 s (the `useSyncStatus` `refetchInterval` pattern; the poll turns itself off once no prioritized/backfill work remains). It has two phases so the prioritized recent window reads differently from the slow backfill: while `prioritized_pending > 0` it shows **"Analyzed X of Y recent matches"** where `X = analyzed`, `Y = analyzed + prioritized_pending`; once that's drained it shows **"Full history loading in the background — X of `known` matches analyzed."** The card removes itself when analysis has caught up (`analyzed ≥ known`, nothing pending). The same counts drive the deep-analysis empty states, which show a live **"{feature} unlocks as matches are analyzed: X of Y done"** line instead of a bare "nothing yet."

**Provisional badge.** Summary-capable views (Overview, Performance › Over time, Insights › Do you tilt?) render a small inline badge — *"early results, deepening as matches are analyzed"* — when the response's top-level `provisional` is true. The Heroes list and the Overview hero cards badge individual rows from `/api/hero-records`' per-hero flags the same way. The badge is **always in the DOM** and only toggles a visibility class, so it appears and disappears with the flag without shifting layout. It never blocks or greys the numbers; those already render honestly from whatever sources are present.

**Mode scope control and Normal-only analytics.** The scope bar carries a **Mode** control (Normal default, Street Brawl the alternative), serialized as the `game_mode` scope param (`"1"` Normal, `"4"` Street Brawl) that every analytical endpoint already honors — this extends the scope-selector table above, which predates the control. Street Brawl is a minor mode and must never mix into Normal analytics. With five tabs the gate applies per tab, per segment, or per section, so muting one analysis never silences its Brawl-friendly neighbors:

- **Render under Brawl:** Overview (minus the Focus areas strip and hero cards, which rest on Normal-only analytics and link into Normal-only views), Performance › Over time, and Insights › Do you tilt? — their endpoints honor `game_mode`, and summaries carry Brawl rows, so a Brawl player still sees their record, KDA, farm stats, and trend lines.
- **Normal-only, full note:** the whole Heroes tab, Performance › Overall and › Laning (per segment), and Improvement show the short "deep analysis is Normal-only" note rather than an empty table. Their population baselines are Normal-centric even where the endpoint honors `game_mode`.
- **Normal-only, compact note:** Insights › Deaths and › Recurring players show a one-line note inside their card while the session analysis beside them keeps rendering.

In every case the gate lives in the wrapper (tab, segment, or section), so the gated view's query never fires under Brawl.

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
8. Every legacy route (`/matchups`, `/items`, `/laning`, `/trends`, `/deaths`, `/tilt`, `/recurring-players`) redirects into the five-tab structure with the scope query string intact.
9. For a user with data, `/heroes` with no `hero_id` opens on the most-played hero's detail — never an empty picker.
