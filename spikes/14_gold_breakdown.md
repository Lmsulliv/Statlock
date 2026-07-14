# Spike 14 — Soul-income (gold) breakdown from stored `stats[]`

**Date:** 2026-07-09 · **Script:** `spikes/14_gold_breakdown.py` (read-only, no
API calls) · **Verified facts recorded in:** `docs/api-findings.md`
("`stats[]` gold breakdown fields").

This writeup holds the *analysis and feature design*. The reusable API facts
(field names, sum-check, coverage) live in `docs/api-findings.md` per hard rule
6; this file is the "what can we build, and how" layer for a later "souls over
time vs your rank" feature.

## Question

`docs/api-findings.md` records that each `match_info.players[].stats[]` snapshot
carries a cumulative gold breakdown (`gold_lane_creep`, `gold_player`,
`gold_neutral_creep`, "…") but never enumerates the full set or checks it
against `net_worth`. From data we *already* store in `matches.raw_json` (no new
fetch): (1) what is the complete field set and what does each source represent;
(2) does it sum to `net_worth` — complete or partial; (3) is the population
reliable enough across match age to build a baseline from all 12 players of
every stored match; (4) how should the feature be designed.

## What the data supports (summary)

Run over **480 stored matches** (self account `891231519`, span 2024-08-25 →
2026-06-27), covering **5,160 player-timelines / 44,624 snapshots**:

| Q | Finding |
| --- | --- |
| (1) Full field set + meaning | **Two layers.** 11 scalar `gold_*` fields (100% present, 0% null, 100% monotone) **plus** a structured `gold_sources[]` array keyed by a `source` enum 1–13. `gold_sources[]` is strictly richer — the scalar fields are roll-ups of sources 1–7; the small passive sources 8–13 appear **only** inside `gold_sources[]`. Boss (`gold_boss`/source 4), denies (`gold_denied`/source 7), and urn/idol-style **treasure** (`gold_treasure`/source 5) are all separable. |
| (2) Sums to `net_worth`? | **Complete for real matches, by mode.** For `game_mode == 1` (normal/ranked), `Σ gold_sources(gold + gold_orbs)` reconstructs `net_worth` within 5% for **97%** of player-timelines (median residual ~1%). `game_mode == 4` (a bot-like mode, 150 matches / 1,199 timelines) carries a **synthetic `net_worth` ramp with an all-zero breakdown** — 0% reconcile. So the breakdown is an effectively-exact decomposition **once game_mode 4 is excluded**. |
| (3) Population reliability across age | **Complete, back to 2024-08.** All 11 scalar fields present & non-null in 100% of snapshots in every month and every era; `gold_sources[]` present for 100% of players in every era; anonymized `account_id == 0` players carry the full breakdown too (100%). **Caveat:** "present" ≠ "meaningful" — game_mode 4's fields are present but zero, so the baseline filter must key on **game_mode**, not on field presence. |

**Load-bearing fact:** income *by source* is given directly per snapshot (it is
not derived), so the feature never needs to reconstruct `net_worth` — it reads
each source's cumulative gold off the series. The only mandatory hygiene step is
excluding game_mode 4.

### Field / source map

| Scalar field | Mean share of final net worth | `gold_sources` id | Nature (from has-kills / has-damage profile) |
| --- | --- | --- | --- |
| `gold_lane_creep` + `_orbs` | 22.3% + 14.7% | 2 | Lane creep last-hits + soul orbs (kills 99.5%, damage 99.9%) |
| `gold_player` + `_orbs` | 15.8% + 0.2% | 1 **and** 6 | Hero combat: source 1 = kills (damage 99.9%), source 6 = assist share (kills but **no** damage) |
| `gold_neutral_creep` + `_orbs` | 7.0% + 0.4% | 3 | Jungle / neutral camps |
| `gold_boss` + `gold_boss_orb` | 6.0% + 0.3% | 4 | Mid-boss / objective (damage 78.5%) |
| `gold_treasure` | 3.2% | 5 | Treasure — urn/idol/sacrifice-shaped income (no kills, no damage) |
| `gold_denied` | 0.5% | 7 | Souls from denying enemy last-hits (no kills, no damage) |
| `gold_death_loss` **(debit)** | −0.8% | — | Souls lost on death; the one debit, subtract it |
| *(no scalar field)* | ~5% combined | 8–13 | Passive/non-combat income (comeback, ability, etc.); no kills/damage; present 94.7% (8–12) / 56.4% (13). This is the "…" remainder in the old note. |

`gold_player = source 1 + source 6`, verified on the fixtures
(`5159 + 4522 = 9681` in `out/02_match_metadata_86714494.json`). The scalar
fields cover only sources 1–7, which is why `Σ` of the scalar credits leaves a
~5% gap that `gold_sources[]` closes.

### Representative divergent (game_mode 4) timeline — why it's excluded

```
match 90057778  duration_s=828  game_mode=4  hero=1
   t_s   net_worth   sum(sources)   resid
   180        5600            0      5600
   360       13000            0     13000
   540       22400           0      22400
   720       34000           0      34000
   828       34000           0      34000
```

The identical round-number `net_worth` ramp (5600 / 13000 / 22400 / 34000)
recurs across different heroes and accounts while every source is zero — a
signature of the non-real mode. Real (game_mode 1) timelines reconcile to ~1%.

## Feature design — "souls over time vs your rank"

### Player series (per-minute cumulative income by source)

Resample each source's cumulative gold onto minute buckets using the existing
honesty rule: take the **latest snapshot with `time_stamp_s <= bucket_end`**
(the same rule the laning report uses for the 540 s sample) and **never
interpolate a fabricated value** — a bucket the series never reached is NULL,
not 0. Structurally this is the deaths-timeline "bin a within-match series by
game-minute vs a population baseline" shape
([api/service.py:207](api/service.py#L207),
[frontend/src/screens/insights/DeathsSection.tsx:119](frontend/src/screens/insights/DeathsSection.tsx#L119)),
not the calendar/rolling bucketing in `stats/trends.py`. Sources to surface:
lane creep, neutral, hero (kills+assists), boss, treasure, denies, passive — a
stacked cumulative area over game time.

### Rank-scoped local baseline

Per hero, average each source's per-minute cumulative across **all 12 players of
every stored real match**, scoped to the viewer's rank band via the existing
team-average-badge predicate `_badge_clause`
([api/queries.py:118](api/queries.py#L118)) — `raw_json` has no per-player
badge (api-findings contradiction #1), only `matches.average_badge_team{0,1}`,
so a player's rank scope is their team's average badge, exactly as the stored
baselines already do. Label it with the established caveat framing — a **live
population baseline of everyone else at this scope**
([OverallSection.tsx:16](frontend/src/screens/performance/OverallSection.tsx#L16)) —
and additionally state it is the **local corpus**, not a global population (same
caveat the Performance baselines carry). **Mandatory filter: `game_mode == 1`**
(exclude game_mode 4's synthetic zero breakdown), matching the Performance
baseline's Normal-only scoping.

### "Largest gap by phase and source" callout

Over (phase bucket × source), compute `max |you − baseline|` and name the
single biggest gap, e.g. "you trail your rank most in **neutral** income during
**mid game**." Verdict math lives in `stats/` per hard rule 1; gate on the same
sample floor the other self-baseline screens use.

### Storage — materialized aggregate, cache-versioned

Decompressing 480 × ~1.5 MB `raw_json` per request is a non-starter (spike 13
made the same call for cross-match work), so materialize:

- A derived per-match table (e.g. `match_income_series(match_id, player_slot,
  account_id, minute_bucket, source, cumulative_gold)`) filled by a
  `derive_income_series(meta)` in `ingest/`, mirroring `derive_kill_events` /
  `derive_laning_stats`, in the one-match transaction, and backfilled over
  history via the existing `reprocess-archive` path (no API calls).
- A rolled-up baseline table keyed `hero × badge-bracket × minute-bucket ×
  source`, refreshed like the other baselines.
- Read side behind a `BaselineCache` ([api/cache.py:57](api/cache.py#L57))
  keyed by `queries.ingest_version` (MAX(match_id),
  [api/queries.py:84](api/queries.py#L84)) — the same version token
  `LIVE_BASELINE_CACHE` uses, since this baseline is computed live from stored
  matches and moves whenever ingest adds one.

**Effort: Medium (≈3–5 days).** Migration + `derive_income_series` + reprocess
wiring + tests (~1–1.5 d); `stats/income.py` pure per-minute bucketing +
baseline + gap callout + tests (~1.5 d); `/api/income-series` + service + CLI
parity (~0.5 d); frontend stacked-area section + tests (~1 d).

## Open questions / risks for an implementation phase

1. **Sources 8–13 are unlabeled.** They reconcile net_worth but we only know
   they're passive (no kills/damage). If the UI wants named passive categories
   (comeback bounty, ability souls, …) someone must map the `source` enum from
   `/v1/assets` or observation. Safe default: fold 8–13 into one "passive"
   series.
2. **game_mode 4 must be filtered everywhere** the baseline or player series is
   built — its fields are *present and non-null but zero*, so a
   presence-based guard silently lets it poison the baseline. Filter on
   `game_mode == 1` (or `!= 4`), the way Performance stays Normal-only.
3. **Minute-bucket resolution vs cadence.** The series is sampled every 180 s
   (then 300 s), so sub-3-minute buckets would be mostly NULL. Bucket at ≥3 min
   or explicitly render the coarse cadence; do not imply per-minute precision
   the data lacks.
4. **Anon players in rank scope.** `account_id == 0` players carry the full
   breakdown and inherit their team-average badge, so they *can* join the
   baseline — decide whether pooling anonymized players into the rank baseline
   is acceptable (they can't be de-duplicated across matches).
5. **`net_worth` share labels are approximate.** Even on real matches the
   per-source sum lands within ~1% median but not exactly; present source
   *shares* as approximate, and compute the baseline from the source fields
   themselves, never from a net_worth residual.
