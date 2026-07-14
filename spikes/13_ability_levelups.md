# Spike 13 — Ability-investment analysis from stored data

**Date:** 2026-07-08 · **Script:** `spikes/13_ability_levelups.py` (read-only,
no API calls) · **Verified facts recorded in:** `docs/api-findings.md` ("Ability
level-up entries in `players[].items[]`").

This writeup holds the *analysis and feature recommendations*. The reusable
API facts live in `docs/api-findings.md` per hard rule 6; this file is the
"should we build it, and how hard" layer.

## Question

The match-metadata `players[].items[]` array mixes shop purchases with ability
level-up entries, and ingest drops the ability entries (`ingest/parse.py:188`).
Can we recover a useful ability-investment analysis from data we *already*
store in `matches.raw_json`, without new fetches? What are the two candidate
features worth, in effort?

## What the data supports (summary)

Run over 480 stored matches (301 with the tracked player), classified against
`spikes/out/06_assets_items.json`:

| Question | Finding |
| --- | --- |
| (1) Separate level-ups from shop buys, ordered by time? | **Yes, cleanly.** `type=="ability"` splits them; only 2 stray item_ids in 301 matches. `game_time_s` orders them. |
| (2) Identify the ability and level reached? | **Yes.** assets `name` + `ability_type` (signature/ultimate/…) name each ability; the played hero owns 1196/1198 ability ids (99.8%). Level = number of entries for that ability (always 1–4). |
| (3) Coverage, incl. old matches? | **Complete.** All 301 self matches carry entries, oldest (2024-10-10) to newest (2026-06-26). No gap. |
| (4) `upgrade_id` / `imbued_ability_id` semantics | `upgrade_id` = opaque upgrade-node token, **not** a level (first point is `0` in only 61% of matches). `imbued_ability_id` lives on **shop** entries (imbue target), always `0` on ability entries. |

**The load-bearing fact for both features:** one ability entry = one point
spent; the ordered sequence of ability `item_id`s **is** the skill-up order,
and the per-ability count is the level. Nothing else needs to be derived.

### Coverage table

| | matches |
| --- | --- |
| stored | 480 |
| decoded from raw_json | 480 |
| self player present | 301 |
| self player with ≥1 ability entry | 301 (100%) |
| self matches with 0 ability entries | 0 |
| distinct abilities/match: 4 / 3 / 6 | 292 / 8 / 1 |
| ability points/match (min–median–max) | 7 – 13 – 16 |

### Representative timeline (match 65070474, hero 81, "signature/ultimate" slots)

```
 time     item_id      upg_id  ability_type  name
 0:29  1011349580  2075342820   signature   Radiant Daggers
 0:30  2590796390  1930440189   ultimate    Shining Wonder
 3:16  2590796390   531959489   ultimate    Shining Wonder
 3:17  3443575800  1484981337   signature   Light Eater
 5:05  1950738949    85783213   signature   Dazzling Trick
 …
```

Ordering by `game_time_s` yields the skill order directly. (Caveat: 327
abilities across the sample banked ≥2 points at the *same* second — order is
arbitrary within a tie, exact between ties.)

## Feature sketch A — ability-order timeline in Match Detail

Show the tracked player's ability level-up order alongside the existing
purchase timeline on `/matches/:matchId`.

**Data path (cheap).** The match-detail service *already* loads and parses
`raw_json` for this exact match (`api/queries.py:950` `match_core` returns
`raw_json`; the service parses the roster and death feed out of it). Add a
small pure helper — call it `parse_ability_timeline(meta, player_slot)` — that
walks `players[].items[]`, keeps `type=="ability"` entries (using the shop-item
id set already loaded at ingest, inverted, or the assets `type` map), and
returns `[{game_time_s, item_id, ability_name, ability_type, point_number}]`
ordered by time. **No schema change, no backfill, no new query** — it reuses
the payload the endpoint already decompressed. The one new dependency is an
`item_id → (name, ability_type)` map for abilities; today the DB `items` table
holds shop items only, so either (a) load ability rows into a reference table
during `refresh_assets`, or (b) resolve names client-side is not possible
(frontend must not compute) — so (a) is the clean choice: a tiny
`ability_assets` reference load, mirroring `tracker/reference.py` `load_items`
but keeping `type in ("ability","weapon")`.

**Frontend.** `frontend/src/screens/MatchDetail.tsx` already renders a
per-player purchase list; add a parallel "Ability order" strip (numbered
pips per ability, grouped by `ability_type`). Pure render of API output — no
stats.

**Effort: Small (≈0.5–1 day).**
- Reference load for ability names (`refresh_assets` + a small table): ~1–2 h.
- `parse_ability_timeline` helper + wire into match-detail service/response +
  test with a stored-match fixture: ~2–3 h.
- Frontend strip + a component test: ~2–3 h.
- Risk: low. Data is 100%-covered and already in memory at request time.

## Feature sketch B — "your usual skill order vs your winning games"

Aggregate, per hero, the tracked player's typical early skill order and compare
won vs lost games (e.g. "in wins you max Bullet Dance first; in losses you
spread points").

**Data path (heavier).** Cross-match aggregation over 301 matches means we do
**not** want to decompress every `raw_json` per request (480 × ~1.5 MB). This
needs the ability points in a queryable table. Follow the existing derived-table
pattern:

- New table `match_ability_points(match_id, player_slot, account_id, item_id,
  point_number, game_time_s)` via a `db/migrations/0NN_*.sql`.
- A `derive_ability_points(meta)` in `ingest/` mirroring how
  `derive_kill_events` / `derive_laning_stats` already pull structured rows out
  of the payload, inserted in the same one-match transaction, and backfilled
  over history through the existing `reprocess_archive` path
  (`ingest/reprocess.py:108`) — which already re-reads every stored match's
  `raw_json`. Backfill is free of API calls.

**Stats layer.** Aggregation lives in `stats/` (hard rule 1). For a given hero,
compute the modal first-N skill order and per-ability point-at-time
distributions, split by `won`. **Honesty matters here:** with a personal
history a hero may have only a handful of games — report raw counts and gate
verdicts behind a minimum-sample floor exactly like the existing self-baseline
screens (`stats/sessions.py`, `stats/recurring.py` use MIN_* floors). "Win-rate
by skill order" is a multiple-comparisons trap; present it as descriptive
("your most common order; your order in wins"), not causal.

**Effort: Medium (≈2–4 days).**
- Migration + `derive_ability_points` + reprocess wiring + tests (test-first per
  acceptance-scenario convention): ~1 day.
- `stats/ability_order.py` (pure aggregation + honesty floors) + tests: ~1 day.
- `/api/ability-order` endpoint + service wiring + CLI parity: ~0.5 day.
- New frontend view (or a section under an existing hero screen) + tests:
  ~0.5–1 day.
- Risk: medium — mostly *presentation honesty* (small per-hero samples), not
  data quality.

## Recommendation

Feature A is a low-risk, high-clarity add that reuses the match-detail payload
already in memory; it's the natural first step and also produces the reference
data (ability names) that Feature B needs. Feature B is worth doing but only
after A lands the ability-name reference load, and it must adopt the same
sample-floor honesty the other self-baseline screens use.

## Open questions / risks for an implementation phase

1. **`upgrade_id` node meaning.** We treat it as opaque. If a future feature
   wants to show *which* upgrade modifier was chosen (not just "a point here"),
   someone must map `upgrade_id` → node name (likely from `/v1/assets` ability
   `properties`/upgrade data). Out of scope for A and B.
2. **Banked-point ties.** Same-second points make strict "Nth point" ordering
   arbitrary within a tie; fine for the level count and the coarse order, but a
   pixel-exact timeline should render tied points as a group, not a sequence.
3. **The 6-ability / 2-mismatch anomaly (~0.3%).** One match showed 6 distinct
   ability ids with 2 not owned by the hero — likely a mid-patch ability swap
   or bad row. Any aggregation should tolerate ≠4 abilities and drop ids whose
   `heroes` list excludes the played hero rather than assuming exactly 4.
4. **Non-self players.** Ability data is present for *all* players in a match,
   so Feature A could show every roster member's skill order, not just self —
   scope decision for the implementation phase.
