# API Findings (verified 2026-06-11)

Verified facts about the live APIs, gathered with the throwaway scripts in
`spikes/`. Every claim cites the raw archived response in `spikes/out/` it
came from. Per CLAUDE.md, this document trumps assumptions in the other
spec docs.

Base URL: `https://api.deadlock-api.com`. All calls below succeeded
unauthenticated at 1 request / 5 s; no 429 or `Retry-After` was ever seen.

---

## Contradictions with the spec docs

Found while verifying; spec docs are unchanged, amendments proposed here.

1. **No per-player badge in match metadata.** `data-model.md` defines
   `match_players.badge` ("player rank at match time"), but the metadata
   response contains no per-player rank anywhere — the only badge fields in
   the entire 1.5 MB response are match-level `average_badge_team0` and
   `average_badge_team1` (verified in two matches:
   `out/02_match_metadata_86714494.json`, `out/05_match_metadata_86707774.json`).
   *Proposed amendment:* drop `match_players.badge`; add
   `average_badge_team0/1` to `matches` (replacing the single
   `average_badge`). For **tracked** accounts only, per-match rank is
   available from `GET /v1/players/{account_id}/mmr-history`, whose rows
   carry `match_id` and `rank` (tier·10+subtier) — a small optional table
   if per-player rank graphs are wanted.

2. **No party information in match metadata.** `data-model.md` defines
   `match_players.party_id`; no party-related key exists in the response
   (same two files). The only related field is the match-level boolean
   `is_high_skill_range_parties`.
   *Proposed amendment:* drop `party_id` (or keep it always-NULL as a
   placeholder until another source appears).

3. **No game build/version in match metadata.** `data-model.md` says "the
   match metadata includes a game version you can map to" `patches`. It
   does not: the only `*version*` keys are `game_mode_version` and the
   path-encoding `match_paths.version`. No `build`/`patch` key exists.
   *Proposed amendment:* bind matches to eras by `start_time` date ranges
   (or equivalently `match_id` ranges — IDs are monotonic and every
   analytics endpoint filters on `min/max_match_id` too). `matches.patch_id`
   cannot be populated from metadata; either drop it or derive it by
   bucketing `start_time` against patch-notes dates.

4. **Lane is an integer, not text.** `match_players.lane TEXT` in the data
   model vs. `assigned_lane` integer in the response (values like 1, 4).
   *Proposed amendment:* `assigned_lane INTEGER`.

5. **Damage/healing totals are not flat per-player fields.**
   `player_damage`, `obj_damage`, `healing` in `match_players` have no
   direct counterparts. They live in the per-player `stats` time series
   (one snapshot per ~180 s); the **last** snapshot holds final totals:
   `player_damage`, `player_healing` (also `self_healing`,
   `player_damage_taken`), and `boss_damage` (closest to objective damage).
   *Proposed amendment:* populate these columns from the final `stats`
   entry and note the source.

6. **Rank brackets are numeric badge values, and the matchup baseline
   endpoint cannot bucket by them.** `baseline_*.rank_bracket TEXT`
   suggests named brackets; actually all rank filtering is numeric
   `min/max_average_badge` (0–116). `hero-counter-stats` (the matchup
   baseline source) has **no** badge bucketing — one snapshot per bracket
   costs one request per bracket. `hero-stats` does offer
   `bucket=avg_badge` in a single call.
   *Proposed amendment:* make `rank_bracket` an integer pair
   (`badge_min`, `badge_max`) or store the single badge level as INTEGER;
   pick a bracket scheme (e.g. 11 tiers) that keeps a baseline snapshot to
   ~11 requests per endpoint.
   *Verified 2026-06-13 (gate spike 08, `out/08_*`):* 12 gapless decade
   brackets `[0,9],[10,19],…,[100,109],[110,116]` re-sum to ~96.0% of the
   single `[0,116]` call on BOTH `hero-counter-stats` and
   `item-stats?bucket=hero` (counter 31.26M/32.55M, item 263.1M/273.9M). The
   `[0,9]` bracket is empty and the decades leave no gaps, so the missing ~4%
   is matches with an unknown (NULL) average badge. Bracketed baselines are
   therefore RATED-only by design; the unrated tail is excluded (no all-ranks
   dual row). `item-stats?bucket=hero` honors `min/max_average_badge` (the
   bracketed sum does not overshoot the full-range sum).
   *Verified 2026-06-15 (gate spike 09, `out/09_*`): the badge filter does NOT
   partition cleanly at single-badge resolution.* Summing 117 per-INTEGER atoms
   (`min_average_badge == max_average_badge == v`, v=0..116) on hero-counter-stats
   reproduces only **85.2%** of the 12-decade total (26.63M vs 31.26M; all 130
   calls returned 200, no errors). i.e. width-1 badge windows DROP ~15% of rated
   matches that the width-10 decades catch — most plausibly because a match's
   *team-average* badge is effectively fractional and rarely lands exactly on an
   integer, so integer-edged brackets leave uncovered gaps between them (avg 9.5
   is in neither [0,9] nor [10,19]). `min_matches=0` does NOT change this (re-run
   confirmed 0.852), so it is not a pair-count threshold effect. Width sweep
   (spike 11, counter, min_matches=0): the leak is monotonic in bracket width --
   more boundaries, more gap loss:

   | width | brackets | sum / decade | sum / full |
   |------:|---------:|-------------:|-----------:|
   |     1 |      117 |        0.852 |      0.818 |
   |     2 |       59 |        0.899 |      0.863 |
   |     3 |       39 |        0.941 |      0.903 |
   |     5 |       24 |        0.959 |      0.921 |
   |    10 |       12 |        1.000 |      0.960 |

   **Consequence:** no sub-decade width reconciles to within 2% of the decade
   total; decade brackets remain the finest CLEAN baseline partition the API
   supports. Per-badge / subrank-granular baseline SCOPES are not feasible via
   this filter without a few-to-15% systematic undercount. (Subrank RANK display
   metadata is unaffected; it doesn't depend on the badge filter.)

7. **Analytics default time window is the last 30 days, not all-time.**
   `min_unix_timestamp` defaults to "30 days ago" per the OpenAPI spec
   (`out/03_openapi.json`). Omitting filters does NOT give an all-time
   baseline; pass an explicit `min_unix_timestamp` (e.g. 0) for that.

8. **Analytics filter `game_mode` is a STRING variant, not the numeric
   match-metadata value.** The `/v1/analytics/*` endpoints expect
   `game_mode=normal` (also `street_brawl`, …); passing the numeric
   `game_mode=1` that match metadata uses returns HTTP 400
   ("unknown variant `1`"). Verified 2026-06-13 (gate spike 08). The
   match-metadata `game_mode` is still the integer axis (1 = Normal,
   4 = Street Brawl — see below). `ingest/maintenance.py` baseline URLs now
   send `game_mode=normal`.

---

## Account ID format and `to_account_id()`

`GET /v1/players/{account_id}/match-history` was called with both formats
(`out/01_match_history_account_id32.json`, `out/01_match_history_steamid64.json`):

| Input | Result |
| --- | --- |
| `891231519` (32-bit account ID) | 200, 322 matches |
| `76561198851497247` (SteamID64) | 200, byte-identical response |

The server normalizes SteamID64 to the 32-bit account ID, but the OpenAPI
spec documents the parameter as "The players `SteamID3`" with
`format: int32` — so SteamID64 acceptance is undocumented behavior.
**Normalize client-side and always send the 32-bit account ID.**

**`to_account_id()` helper (Phase 1 utility):** friends will paste any of:

- 32-bit account ID / friend ID: use as-is (`891231519`)
- SteamID64: `account_id = steamid64 - 76561197960265728`
  (76561198851497247 → 891231519, verified)
- Profile URL `steamcommunity.com/profiles/<steamid64>`: extract the
  number, subtract as above
- Vanity URL `steamcommunity.com/id/<name>`: not resolvable offline —
  needs Steam's `ResolveVanityURL` (requires an API key) or
  `GET /v1/players/steam-search?search_query=...` on deadlock-api;
  acceptable to reject with a helpful message at first.

Heuristic: values ≥ 76561197960265728 are SteamID64; smaller positive
integers are account IDs.

---

## Endpoints verified

| Endpoint | Purpose | Raw sample |
| --- | --- | --- |
| `GET /v1/players/{account_id}/match-history` | discovery loop | `out/01_match_history_account_id32.json` |
| `GET /v1/matches/{match_id}/metadata` | full match ingestion | `out/02_match_metadata_86714494.json` |
| `GET /v1/analytics/hero-counter-stats` | matchup baselines | `out/03c_counter_default.json` |
| `GET /v1/analytics/item-stats` | item baselines | `out/03d_item_stats_hero7.json` |
| `GET /v1/analytics/hero-stats` | per-badge hero baselines | `out/03d_hero_stats_badge_bucket.json` |
| `GET /v1/assets/ranks` | badge → name mapping | `out/03c_ranks.json` |
| `GET /v1/patches/big-days` | big-patch dates | `out/03c_big_days.json` |
| `GET https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=1422450` | era candidates | `out/04_steam_news.json` |

Also present in the OpenAPI spec (103 paths, `out/03_openapi.json`) and
relevant later: `/v1/players/{account_id}/mmr-history` (rank per match —
the Overview screen's MMR graph), `/v1/players/{account_id}/enemy-stats`
and `/mate-stats`, `/v1/assets/heroes`, `/v1/assets/items`, `/v1/patches`
(Valve forum RSS as JSON), `/v2/patches`, `/v1/matches/{match_id}/metadata/raw`.

---

## Match history response shape

A plain JSON array (322 entries for account 891231519 — apparently the
full history, no pagination parameters exist; only a `force_refetch` flag
that triggers stricter rate limits). Fields per row, all observed non-null
unless noted:

```text
account_id, match_id, hero_id, hero_level, start_time (unix s),
game_mode (int, 1=normal), match_mode (int), player_team (0/1),
player_kills, player_deaths, player_assists, denies, net_worth,
last_hits, team_abandoned (bool), abandoned_time_s (null),
match_duration_s, match_result, objectives_mask_team0/1,
brawl_score_team0/1 (null), brawl_avg_round_time_s (null)
```

**`match_result` is the winning team's number, not a won-flag.** Verified
by spike 05: match 86707774 had `player_team=0, match_result=1` and its
metadata says `winning_team=1` (`out/05_match_metadata_86707774.json`).
So: `won = (player_team == match_result)`. Note history rows already carry
enough (hero, K/D/A, result) for the Overview's "last 10 matches" without
metadata.

**`game_mode` values: `1` = Normal, `4` = Street Brawl** (verified 2026-06-13
from account 891231519's history). The `brawl_score_team0/1` and
`brawl_avg_round_time_s` fields are populated *only* on `game_mode=4` rows and
null on `game_mode=1` rows, which is what identifies Brawl: match 86704689 has
`game_mode=4` with `brawl_score_team0=3, brawl_score_team1=1,
brawl_avg_round_time_s=185`, while matches 86707774 and 86714494 have
`game_mode=1` and null brawl fields. `match_mode` (separate axis, e.g. ranked
vs unranked) was `1` on every observed row, so its value mapping is not yet
verified. **Implication for analytics:** matchup/lane analysis is meaningful
only for the standard mode, so personal queries default to `game_mode=1` and
must never lump Brawl in with Normal. Other `game_mode` values (sandbox, bots,
etc.) are not yet observed — treat anything other than 1/4 as unverified.

---

## Match metadata: field population

`GET /v1/matches/{match_id}/metadata` returns `{match_info, hero_build_ids,
banned_hero_ids}`, ~1.2–1.6 MB per match. Verified against two matches.

Match-level (`match_info`), mapping to the `matches` table:

| Spec column | Found? | Actual source |
| --- | --- | --- |
| `match_id`, `start_time`, `duration_s` | yes | same names (`start_time` unix int) |
| `game_mode` | yes | `game_mode` + `match_mode` (ints) |
| `winning_team` | yes | `winning_team` (0/1; also `match_outcome`) |
| `patch_id` (game version) | **no** | absent — see contradiction 3 |
| `average_badge` | partly | `average_badge_team0`, `average_badge_team1` (e.g. 53 = tier 5 subtier 3) |

Per-player (`match_info.players[]`, 12 entries), mapping to `match_players`:

| Spec column | Found? | Actual source |
| --- | --- | --- |
| `account_id`, `hero_id`, `team` | yes | same names (+ `player_slot`) |
| `lane` | yes | `assigned_lane` (integer) |
| `party_id` | **no** | absent — see contradiction 2 |
| `badge` | **no** | absent — see contradiction 1 |
| `kills`, `deaths`, `assists` | yes | same names |
| `net_worth`, `last_hits`, `denies` | yes | same names |
| `player_damage`, `obj_damage`, `healing` | derived | last entry of `stats[]`: `player_damage`, `boss_damage`, `player_healing` |
| `won` | derived | `team == winning_team` |

Item purchases (`players[].items[]`, ~40 entries/player):

- `game_time_s` (purchase time, present), `item_id`, `sold_time_s` (0 =
  never sold, >0 = seconds into match when sold), `flags`,
  `imbued_ability_id`, `upgrade_id`, `upgrade_info`.
- **Caution:** the array mixes shop purchases with ability/level-up entries
  (same `item_id` recurring with changing `upgrade_id`). Filter entries by
  membership in `/v1/assets/items` shop items before inserting into
  `match_item_purchases`.

Also present and worth knowing about (lives in `raw_json` for later):
`death_details[]` (timestamps + killer + positions), `stats[]` time series
(net worth, damage, healing, accuracy every ~3 min), `damage_matrix`,
`match_paths` (positions), `objectives[]`, `mid_boss[]`, `accolades[]`.

---

## Analytics endpoints: filters and rank granularity

All analytics endpoints share these query params (full list per endpoint
in `out/03_openapi.json`):

- `min/max_unix_timestamp` — filter by match start time. **Date-range
  filtering works**, so eras map directly onto explicit timestamp ranges.
  Verified shrinkage on `hero-counter-stats`: default 30 d ≈ 33.4 M
  matches_played summed, last 7 d ≈ 5.87 M, 7 d + Eternus-only ≈ 0.16 M
  (`out/03c_counter_default.json`, `..._7d.json`, `..._7d_eternus.json`).
- `min/max_average_badge` (0–116) — see badge encoding below.
- `min/max_match_id` — alternative era boundary (IDs are monotonic).
- `min/max_duration_s`, `min_matches`, `account_id(s)`, `game_mode`
  (STRING variant here — `normal`/`street_brawl`/…, not the numeric
  match-metadata value; see contradiction 8).
- **No patch/version filter exists.** Eras must be expressed as timestamp
  or match-id ranges. Default window is the last 30 days (contradiction 7).

**Badge encoding:** `badge = tier * 10 + subtier`, subtier 1–6. Tiers from
`/v1/assets/ranks` (`out/03c_ranks.json`): 0 Obscurus, 1 Initiate,
2 Seeker, 3 Alchemist, 4 Arcanist, 5 Ritualist, 6 Emissary, 7 Archon,
8 Oracle, 9 Phantom, 10 Ascendant, 11 Eternus. Observed buckets in live
data: 12–116, 65 distinct values (no tier 0, no badge 11 in that window),
see `out/03d_hero_stats_badge_bucket.json`. **Finest granularity: a single
badge value** (tier+subtier) via equal `min_average_badge` and
`max_average_badge`. Note the filter applies to the *match's team-average*
badge, not to individual players.

**`/v1/assets/ranks` shape (verified 2026-06-15, spike 10, `out/10_assets_ranks.json`):**
12 entries, each `{tier, name, color, images}`. The `images` object carries
the badge art. All 12 tiers have `images.large` / `large_webp`
(`.../ranks/rank{tier}/badge_lg.png`). **Tiers 1–11** additionally have
per-subrank art `images.large_subrank{1..6}` (+ `_webp`, + `small_subrank{1..6}`
variants), following the pure pattern
`.../ranks/rank{tier}/badge_lg_subrank{subtier}.png`. **Tier 0 (Obscurus) has
NO subrank art** (only `large`/`small`). Subrank URLs follow a pure
tier+subtier pattern if ever needed (subrank 6's badge is `badge_lg_subrank6.png`,
which the asset depicts with stars). **Currently unused:** because the badge
filter only partitions cleanly at decade/tier granularity (finding 6), the rank
selector and baselines are tier-granular, so `/api/ranks` derives only the
per-tier `badge_lg.png` and does not expand subranks.

Endpoint specifics:

- **`/v1/analytics/hero-counter-stats`** — one row per
  `(hero_id, enemy_hero_id)` with `wins`, `matches_played` (exactly what
  `baseline_hero_matchups` needs) plus aggregate K/D/A, net worth,
  obj_damage, etc. for both sides. Has `same_lane_filter` (lane-opponent
  baselines!) and `min_matches`. **No bucket param**, so fetching N rank
  brackets costs N requests (~1,406 rows, ~550 KB each).
- **`/v1/analytics/item-stats`** — one row per item (146 rows for
  hero_id=7) with `wins`, `losses`, `matches`, `players`,
  `avg_buy_time_s` (the spec's `avg_purchase_s`), `avg_sell_time_s`,
  and relative (% of match duration) variants. Filter by `hero_id`. Its
  `bucket` enum is `no_bucket, hero, team, start_time_*, game_time_*,
  net_worth_by_*` — **no `avg_badge` bucket** (a literal
  `bucket=avg_badge` call returns 400, `out/03c_item_stats_badge_bucket.json`),
  so per-bracket item baselines also cost one request per bracket.
  - **`bucket=hero` works and gives per-hero-per-item rows in ONE call**
    (verified 2026-06-13, `out/07_item_stats_bucket_hero.json`): 5,892 rows,
    ~1.4 MB, the **`bucket` field holds the hero_id** (38 distinct heroes,
    values 1–81, all ≤ 83; ~155 items each). Row keys are unchanged from
    `no_bucket` (`item_id, wins, losses, matches, players, avg_buy_time_s`,
    relative variants) — there is no separate `hero_id` key, you read it
    from `bucket`. This means `baseline_hero_item_stats` can be filled with
    **one request per era** (mapping `bucket → hero_id`,
    `avg_buy_time_s → avg_purchase_s`), not one per hero per era. Still no
    badge bucketing, so per-bracket item baselines would remain N requests.
- **`/v1/analytics/hero-stats`** — supports `bucket=avg_badge`: one call
  returns per-hero-per-badge rows (2,470 rows, ~1.1 MB) with
  `wins/losses/matches` and totals, plus `matches_per_bucket`. Useful for
  overall hero baselines at the finest granularity in a single request.

Budget note for the nightly baseline snapshot: at tier granularity
(11 brackets), matchups + items is roughly 11 + 11 requests, about
2 minutes at the 1-per-5 s limit. Finest granularity (66 brackets) would
be ~11 minutes. Both fine.

---

## Patch sources for era detection

**Steam News API** (`GetNewsForApp`, appid 1422450 confirmed in the
response, `out/04_steam_news.json`):

- No key required; `?appid=1422450&count=30&maxlength=0` returns mixed
  feeds — filter to `feedlabel == "Community Announcements"`
  (`feedname == "steam_community_announcements"`) to get only Valve posts.
- Item fields: `gid, title, url, is_external_url, author, contents,
  feedlabel, date (unix), feedname, feed_type, appid`.
- `contents` is BBCode-ish on a single line: paragraphs as `[p]...[/p]`,
  change lines start with `- `. No newlines, no `<br>`, no `<li>`.
  Change-line heuristic that works: count regex matches of `\[p\]\s*-\s`.
- Valve's own titles already classify updates:

| Post | Change lines | Chars |
| --- | --- | --- |
| Gameplay Update - 03-06-2026 (major) | 1177 | 92,167 |
| Gameplay Update - 05-22-2026 (major) | 307 | 25,897 |
| Gameplay Update - 04-30-2026 (major) | 162 | 12,211 |
| Minor Update - 06-04-2026 | 14 | 1,348 |
| Minor Update - 05-25-2026 | 1 | 890 |
| Apollo - A Cut Above (hero release) | 0 | 1,295 |

  Calibration: the title prefix (`Gameplay Update` vs `Minor Update`) is a
  near-perfect major/minor classifier on its own; a change-line count
  above ~100 separates the same classes. Hero-release posts have ~0 change
  lines but are era-worthy — score titles that aren't `Minor Update`
  generously.

**deadlock-api patch endpoints** (supplement): `/v1/patches` returns the
Valve forum RSS as JSON (`title, pub_date, link, content_encoded`);
`/v1/patches/big-days` returns just the big-patch dates
(`out/03c_big_days.json`) — but it appears to **lag**: its latest entry is
2026-03-11 even though Gameplay Updates shipped 2026-04-30 and 2026-05-22.
Use Steam News as the primary source, big-days as a sanity cross-check.

---

## Open questions (not yet verified — do not assume)

- Which team number is Amber vs. Sapphire (`data-model.md` says 0 = Amber);
  nothing in the responses names the teams.
- Whether 322 history entries is truly the complete history or capped.
- Published rate limits: nothing was throttled at 1 req/5 s; no limit
  headers were observed on responses.

---

## Assets endpoints (verified 2026-06-11)

Raw samples: `spikes/out/06_assets_heroes.json`, `spikes/out/06_assets_items.json`.

### `GET /v1/assets/heroes` — 61 entries

Fields always present: `id` (INTEGER), `name` (TEXT, human-readable e.g. "Infernus"),
`class_name` (TEXT, internal e.g. "hero_inferno"), `images` (object),
`disabled` (bool), `in_development` (bool), `player_selectable` (bool).

Image URL key: `images.icon_hero_card` is the canonical card art URL (present in
58/61). `images.icon_hero_card_webp` is the WebP variant. Other keys:
`icon_image_small`, `minimap_image`, `top_bar_vertical_image`, etc.

Loader mapping to `heroes`: `id → hero_id`, `name → name`,
`images.icon_hero_card → image_url` (may be NULL for 3 unreleased heroes).

IDs are small non-sequential integers: 1–83 range with gaps.

### `GET /v1/assets/items` — 726 entries; 251 are shop items

**Discriminator: `type == "upgrade"`** → shop/purchaseable item. The other 475
entries are `"ability"` (hero abilities, 389) or `"weapon"` (hero gun items, 86).
Only `"upgrade"` entries ever have `item_tier` or `item_slot_type`.

**Filter for `match_item_purchases`**: join `item_id` against the set of IDs where
`type == "upgrade"` (resolves the open question about distinguishing shop
purchases from ability entries in per-player `items[]`).

Fields on every `"upgrade"` entry (251 total):
`id` (INTEGER), `name` (TEXT, human-readable, always differs from `class_name`),
`item_slot_type` (TEXT: `"weapon"`, `"vitality"`, or `"spirit"`),
`item_tier` (INTEGER: 1–5), `cost` (INTEGER, soul cost).
`image` present in 232/251; `shop_image` present in 184/251 — prefer `shop_image`
when loading `items.image_url`, fall back to `image`.

Active vs disabled upgrades: `shopable == True, disabled == null/False` (173
items) are live; `shopable == False, disabled == True` (78 items) are disabled
and should still be loaded (needed to parse older matches).

Loader mapping to `items`: `id → item_id`, `name → name`,
`item_tier → tier`, `item_slot_type → slot_type`,
`shop_image ?? image → image_url`.

---

## Not-parsed / unfetchable matches (spike, verified 2026-06-19)

Gathered by the throwaway `scripts/spike_not_parsed.py` to decide the
deferral detection rule and give-up policy. Raw bodies archived in
`spikes/out/notparsed_*`.

**The metadata endpoint signals "no data for this match" with HTTP 400,
NOT 404.** Observed body shape:
`{"error":"Match salts for match <id> cannot be fetched","status":400}`.
No 404 / 202 / 425 / 409 / 429 was seen. ("Salts" are the per-match
metadata/replay decryption keys deadlock-api fetches from Steam before it can
return metadata — see the mechanism note in the follow-up section below.)

> **Correction (see follow-up below).** An earlier draft of this section
> claimed these matches' "replays Valve has purged" / are "genuinely gone".
> That was an over-reach: the passive 400 does not say *why* salts are missing,
> and the (a) sample below was fired too fast to trust. The follow-up spike
> tests recoverability properly.

### (a) Are existing `unavailable` rows recoverable? (n=12)

| result | count |
| --- | ---: |
| 400 "salts cannot be fetched" | 12 |

Recovered (now 200): none. **But this result is not trustworthy:** all 12
probes went out in ~1 minute, and fetching salts for an uncached match routes
through Steam, which is rate-limited to **10 req / 30 min per IP** (see
mechanism note). So most of these 400s are likely the rate limit, not missing
data. The follow-up spike re-tests within the limit.

### (b) Signal for the newest matches from live account history

| match_id | already_fetched | status | category | headers |
| ---: | :---: | ---: | --- | --- |
| 89193045 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |
| 89103814 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |
| 89034903 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |
| 88653444 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |
| 88639055 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |
| 88579983 | True | 200 | 200 (data available) | Content-Type=application/json, Cache-Control=public, max-age=604800 |

### (c) Does re-requesting change the answer?

No not-ready match was available to re-probe in this run.

---

## How salts/metadata fetching actually works (from OpenAPI, verified 2026-06-19)

From `spikes/out/03_openapi.json` (the live spec), the salts pipeline is:

- **`GET /v1/matches/{match_id}/metadata`** has a `disable_steam` flag
  (default *off*): "skip the Steam fallback when the metadata is not available
  in S3 and return an error instead." So our plain `/metadata` call already
  *does* try Steam. The 400 means even the Steam fallback couldn't get salts.
- **`GET /v1/matches/{match_id}/salts`** is the direct salts fetch. Its
  documented rate limits: **DB 100 req/s, but Steam fallback 10 req/30 min per
  IP** (and 10 req/10 s globally). Uncached old matches always hit the Steam
  path, so a burst of probes self-inflicts 400s.
- **`POST /v1/matches/salts` ("Match Salts Ingest")** lets community members
  submit salts they harvested from their own client — this is how most matches
  get salts in the first place (confirms: salts exist for a match only once
  someone who was in it, running the ingest tool, surfaces it, or Steam still
  serves it).
- **`force_refetch`** is a flag on `/v1/players/{id}/match-history` only
  ("refetch the match history from Steam"). It refreshes the match *list*, NOT
  per-match salts — so it is **not** a lever for recovering a specific match.

## Recoverability re-test, within the rate limit (spike #2, verified 2026-06-19)

`scripts/spike_salts_recovery.py`: 4 `GET /v1/matches/{id}/salts` calls (Steam
fallback on), well spaced, ~30 min after spike #1 so the Steam window had reset,
on the 4 newest `unavailable` matches (ids ~28.2M).

| context | result |
| --- | --- |
| `GET /v1/matches/recently-fetched` | 200, fresh match 89.5M — the fetch pool is **live** |
| 4× `GET /v1/matches/{id}/salts` (spaced) | **4/4 HTTP 400 "salts cannot be fetched"**, **no 429**, no rate-limit headers |

**What this supports (and what it doesn't):** with the pool demonstrably live
and the rate limit *not* blown (no 429), the direct salts fetch still fails for
these old matches. So for them, deadlock-api currently cannot obtain salts from
Steam — most likely because Valve's game coordinator no longer serves
salts/replays for matches this old. This is *not* proof every old match is
permanently gone (n=4), and such a match could still be recovered if a
participant submits salts via `POST /v1/matches/salts` — but our worker can't
trigger that.

**Implications for the drain loop (deferral, ingest/drain.py):**

- The 400-salts signal is **ambiguous by design** between "too new, salts not
  harvested yet" (recoverable soon) and "too old, Steam won't serve them"
  (effectively unrecoverable by us). There is no field that separates them.
- Deferral handles both correctly *because* it retries slowly: hourly retries
  are ~1 req/hour, far under the 10 req/30 min Steam-salts limit, so (i) a NEW
  match's salts get fetched once the pool reaches them, and (ii) 400s that were
  merely **rate-limit fallout from a bulk backfill** (the common case while
  importing an account) clear themselves on a later, unhurried retry. Old,
  truly-unfetchable matches keep failing and are given up after MAX_DEFER_AGE_S.
- This is the corrected justification for the design: NOT "those matches are
  proven gone," but "recoverable ones recover within the retry window; the rest
  age out." The existing `unavailable` rows are therefore left as-is (no requeue
  migration); the nightly revive still re-probes them slowly.
