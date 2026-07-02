"""Assembly layer: the shared "backend functions".

Takes a connection + a Scope, runs api.queries, applies the pure math from
stats/, and returns plain JSON-serializable dicts. Both api.app (FastAPI) and
stats/__main__.py (the CLI) call these, so the two can never disagree on a
number -- that's the whole point of the stepping-stone CLI.

Imports stats, tracker, and ingest (to reuse account registration) only; never
FastAPI, so the CLI stays lightweight.
"""
import dataclasses
import json
import math
import sqlite3

from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_FLOOR,
    VERDICT_LEANING_STRENGTH,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
    mean_interval,
    mean_verdict,
    shrunk_rate,
    verdict,
    wilson_interval,
)
from stats.sessions import (
    SESSION_GAP_S,
    by_loss_streak,
    by_session_index,
    group_sessions,
)
from stats.deaths import build_timeline
from stats.recurring import MIN_CO_OCCURRENCE, split_recurring
from stats.trends import (
    TRENDS_WINDOW_DEFAULT,
    bucket_by_calendar,
    rolling_windows,
)

from ingest import accounts as ingest_accounts
from ingest.util import DEFAULT_USER_ID, utcnow

from api import cache
from api import match_detail as detail
from api import queries
from api.scope import Scope

_RATE_DP = 4   # decimal places for rates (keeps JSON clean and deterministic)
_TIME_DP = 1   # decimal places for purchase-timing seconds
_METRIC_DP = 2  # decimal places for continuous metrics (net worth/min, KDA, ...)


def _round(x: float | None, ndigits: int = _RATE_DP) -> float | None:
    return None if x is None else round(x, ndigits)


def _resolved(conn: sqlite3.Connection, scope: Scope) -> Scope | None:
    """Scope with account_id filled from the is_self account. None if no account
    can be resolved (empty DB) -- callers return an empty state."""
    if scope.account_id is not None:
        return scope
    account_id = queries.resolve_self_account_id(conn)
    if account_id is None:
        return None
    return dataclasses.replace(scope, account_id=account_id)


def _stat_fields(wins: int, games: int, global_wins: int, global_matches: int) -> dict:
    """The shared statistics block: winrate + Wilson interval + shrinkage +
    verdict. Used identically by matchup and item rows."""
    low, high = wilson_interval(wins, games)
    winrate = wins / games if games else None
    fields = {
        "winrate": _round(winrate),
        "ci_low": _round(low),
        "ci_high": _round(high),
        "global_matches": global_matches,
    }
    if global_matches > 0:
        global_rate = global_wins / global_matches
        adjusted = shrunk_rate(wins, games, global_rate)
        fields.update({
            "global_rate": _round(global_rate),
            "adjusted_rate": _round(adjusted),
            # delta is the shrinkage-adjusted gap (spec's "Adjusted delta");
            # raw_delta is the plain personal-minus-global the user also wanted.
            "delta": _round(adjusted - global_rate),
            "raw_delta": _round(winrate - global_rate) if winrate is not None else None,
            "verdict": verdict(wins, games, global_rate),
        })
    else:
        # No baseline for this scope -> nothing to compare against.
        fields.update({
            "global_rate": None, "adjusted_rate": None, "delta": None,
            "raw_delta": None, "verdict": VERDICT_NOT_ENOUGH_DATA,
        })
    return fields


def _significant(row: dict) -> bool:
    """A confirmed call (used by the items sort and the improvement digest)."""
    return row["verdict"] in (VERDICT_CLEAR_STRENGTH, VERDICT_CLEAR_WEAKNESS)


# ── Matchups ─────────────────────────────────────────────────────────────────

def matchups(conn: sqlite3.Connection, scope: Scope,
             hero_id: int | None = None) -> list[dict]:
    """One row per enemy hero (optionally restricted to a hero you play).

    When hero_id is given, each row's baseline is the (hero, enemy) global rate.
    When it is omitted, personal counts are aggregated across the heroes you
    actually played, and the baseline is summed over exactly those same
    (your hero, enemy) pairs so personal and global stay comparable.
    """
    scope = _resolved(conn, scope)
    if scope is None:
        return []

    personal = queries.personal_matchups(conn, scope, my_hero_id=hero_id)
    snapshot = queries.latest_snapshot_id(conn)
    baseline = cache.cached_baseline_matchups(conn, scope, snapshot) if snapshot else {}
    names = queries.hero_names(conn)
    images = queries.hero_images(conn)

    # Kill trades per enemy hero (design note 3), from the same scope. A separate
    # query so it can't perturb the games-faced counts above; raw counts only --
    # any future kill-trade verdict would belong in stats/, not here.
    trades = {t["enemy_hero"]: t
              for t in queries.personal_kill_trades(conn, scope, my_hero_id=hero_id)}

    agg: dict[int, dict] = {}
    for row in personal:
        enemy = row["enemy_hero"]
        bucket = agg.setdefault(enemy, {"games": 0, "wins": 0, "gw": 0, "gm": 0})
        bucket["games"] += row["games"]
        bucket["wins"] += row["wins"] or 0
        b = baseline.get((row["my_hero"], enemy))
        if b:
            bucket["gw"] += b["wins"]
            bucket["gm"] += b["matches"]

    rows = []
    for enemy, b in agg.items():
        # Every aggregated enemy is returned; min_games is an "enough to judge"
        # line the UI reads off the verdict, not a row filter. Thin rows still
        # resolve to not_enough_data via the stats floor.
        t = trades.get(enemy, {})
        row = {
            "enemy_hero_id": enemy,
            "enemy_hero_name": names.get(enemy, str(enemy)),
            "enemy_hero_image_url": images.get(enemy),
            "games": b["games"],
            "wins": b["wins"],
            "kills_by_them_on_you": t.get("kills_by_them_on_you", 0),
            "kills_by_you_on_them": t.get("kills_by_you_on_them", 0),
        }
        row.update(_stat_fields(b["wins"], b["games"], b["gw"], b["gm"]))
        rows.append(row)

    # Default order is alphabetical by enemy hero; the UI re-sorts on click.
    rows.sort(key=lambda r: r["enemy_hero_name"].lower())
    return rows


# ── Death patterns (kill_events aggregated for coaching) ─────────────────────

def death_patterns(conn: sqlite3.Connection, scope: Scope) -> dict:
    """Coaching aggregates over the scoped match set's kill_events:

    - `by_enemy_hero`: which enemy heroes kill you most, as RAW deaths plus the
      games you faced them (so a big count off many games reads differently than
      off a few). No verdict / interval: there is no stored per-matchup death
      baseline, so -- like the match-detail kill trades -- we never fabricate one.
      Any future death verdict would need a baseline and would live in stats/.
    - `by_damage_source`: which enemy heroes deal you the most damage, as average
      GROSS damage per game (damage_matrix, materialized into
      damage_taken_sources). Like by_enemy_hero it is a raw relative ranking with
      no verdict -- damage_matrix is pre-mitigation and does not reconcile with the
      net damage-taken total (api-findings), so there is no honest baseline.
    - `timeline`: your deaths bucketed into game-minute bins, each compared to a
      LIVE population baseline (everyone else at this scope). Fewer deaths is good,
      so higher_is_better=False flips the verdict tier -- identical to the `deaths`
      metric on Performance -- and the same _metric_fields machinery attaches the
      t-interval + verdict. A bin with no population comes back not_enough_data.
    """
    empty = {"by_enemy_hero": [], "by_damage_source": [], "timeline": [],
             "total_deaths": 0, "games": 0}
    scope = _resolved(conn, scope)
    if scope is None:
        return empty
    games = queries.scoped_match_count(conn, scope)
    if games == 0:
        return empty

    names = queries.hero_names(conn)
    images = queries.hero_images(conn)

    by_hero = []
    for r in queries.death_by_enemy_hero(conn, scope):
        enemy = r["enemy_hero"]
        by_hero.append({
            "enemy_hero_id": enemy,
            "enemy_hero_name": names.get(enemy, str(enemy)),
            "enemy_hero_image_url": images.get(enemy),
            "deaths": r["deaths"] or 0,
            "games_faced": r["games_faced"],
        })
    # Worst killer first; ties broken by games faced, then name for a stable order.
    by_hero.sort(key=lambda r: (-r["deaths"], -r["games_faced"],
                                r["enemy_hero_name"].lower()))

    by_damage = []
    for r in queries.damage_taken_by_enemy_hero(conn, scope):
        enemy = r["enemy_hero"]
        total = r["total_damage"] or 0
        faced = r["games_faced"]
        by_damage.append({
            "enemy_hero_id": enemy,
            "enemy_hero_name": names.get(enemy, str(enemy)),
            "enemy_hero_image_url": images.get(enemy),
            "total_damage": total,
            "games_faced": faced,
            "avg_per_game": _round(total / faced, 1) if faced else 0.0,
        })
    # Hardest-hitting enemy first; ties broken by games faced, then name.
    by_damage.sort(key=lambda r: (-r["avg_per_game"], -r["games_faced"],
                                  r["enemy_hero_name"].lower()))

    death_times = [(d["match_id"], d["game_time_s"])
                   for d in queries.personal_death_times(conn, scope)]
    pop_games, pop_by_minute = queries.population_death_timeline(conn, scope)
    timeline = []
    for b in build_timeline(games, death_times, pop_by_minute, pop_games):
        fields = _metric_fields(b["per_game_counts"], b["baseline_mean"],
                                b["baseline_games"], higher_is_better=False)
        timeline.append({"minute": b["minute"], "label": b["label"],
                         "deaths": b["deaths"], **fields})

    total_deaths = sum(b["deaths"] for b in timeline)
    return {"by_enemy_hero": by_hero, "by_damage_source": by_damage,
            "timeline": timeline, "total_deaths": total_deaths, "games": games}


# ── Items (per hero) ─────────────────────────────────────────────────────────

def items(conn: sqlite3.Connection, scope: Scope, hero_id: int) -> list[dict]:
    """One row per item for a chosen hero, plus a purchase-timing delta."""
    scope = _resolved(conn, scope)
    if scope is None:
        return []

    personal = queries.personal_item_stats(conn, scope, hero_id)
    snapshot = queries.latest_snapshot_id(conn)
    baseline = cache.cached_baseline_item_stats(conn, scope, hero_id, snapshot) if snapshot else {}
    names = queries.item_names(conn)
    images = queries.item_images(conn)

    rows = []
    for p in personal:
        # min_games no longer drops thin item rows; they appear without a verdict
        # (see matchups()). The UI renders the not_enough_data state honestly.
        item_id = p["item_id"]
        b = baseline.get(item_id, {})
        gw, gm = b.get("wins", 0), b.get("matches", 0)
        personal_buy = p["avg_purchase_s"]
        global_buy = b.get("avg_purchase_s")
        timing_delta = (
            personal_buy - global_buy
            if personal_buy is not None and global_buy is not None else None
        )
        row = {
            "item_id": item_id,
            "item_name": names.get(item_id, str(item_id)),
            "item_image_url": images.get(item_id),
            "games": p["games"],
            "wins": p["wins"],
            "avg_purchase_s": _round(personal_buy, _TIME_DP),
            "global_avg_purchase_s": _round(global_buy, _TIME_DP),
            "purchase_timing_delta_s": _round(timing_delta, _TIME_DP),
        }
        row.update(_stat_fields(p["wins"], p["games"], gw, gm))
        rows.append(row)

    rows.sort(key=lambda r: (0 if _significant(r) else 1,
                             -(abs(r["delta"]) if r["delta"] is not None else 0.0),
                             -r["games"], r["item_id"]))
    return rows


# ── Performance (continuous metrics per hero and overall) ────────────────────

# mean_verdict is value-neutral ("strength" = above baseline). For a metric where
# lower is better (deaths) we flip the tier so a verdict always reads as good/bad,
# not above/below -- keeping green = "good" and letting one tested math function
# serve every metric (the math never learns which way a metric points).
_VERDICT_FLIP = {
    VERDICT_CLEAR_STRENGTH: VERDICT_CLEAR_WEAKNESS,
    VERDICT_CLEAR_WEAKNESS: VERDICT_CLEAR_STRENGTH,
    VERDICT_LEANING_STRENGTH: VERDICT_LEANING_WEAKNESS,
    VERDICT_LEANING_WEAKNESS: VERDICT_LEANING_STRENGTH,
    VERDICT_NOT_ENOUGH_DATA: VERDICT_NOT_ENOUGH_DATA,
}


def _finite(x: float | None) -> float | None:
    """A t-interval is (-inf, +inf) at n == 1; JSON can't carry infinities, so a
    non-finite bound becomes None and the frontend reads it as 'no interval yet'."""
    return None if x is None or math.isinf(x) else x


def _metric_fields(values: list[float], baseline_mean: float | None,
                   baseline_n: int, higher_is_better: bool) -> dict:
    """Continuous-metric analogue of _stat_fields: personal mean + t-interval +
    a direction-aware verdict against a baseline mean. `values` is the per-match
    sample for one metric (NULLs already dropped). `games` is that sample size, so
    the UI's "need more games" wording lines up with the stats floor the verdict
    uses. With no baseline (None mean or empty population) the row is personal-only,
    marked not_enough_data, so the screen never compares against nothing."""
    n = len(values)
    raw_mean = ci_low = ci_high = None
    if n:
        raw_mean, low, high = mean_interval(values, 0.95)
        ci_low, ci_high = _finite(low), _finite(high)

    fields = {
        "games": n,
        "mean": _round(raw_mean, _METRIC_DP),
        "ci_low": _round(ci_low, _METRIC_DP),
        "ci_high": _round(ci_high, _METRIC_DP),
    }
    if baseline_mean is not None and baseline_n > 0:
        tier = mean_verdict(values, baseline_mean) if n else VERDICT_NOT_ENOUGH_DATA
        if not higher_is_better:
            tier = _VERDICT_FLIP[tier]
        fields.update({
            "baseline_mean": _round(baseline_mean, _METRIC_DP),
            "baseline_games": baseline_n,
            "delta": _round(raw_mean - baseline_mean, _METRIC_DP) if raw_mean is not None else None,
            "verdict": tier,
        })
    else:
        fields.update({"baseline_mean": None, "baseline_games": 0,
                       "delta": None, "verdict": VERDICT_NOT_ENOUGH_DATA})
    return fields


def _continuous_rows(conn: sqlite3.Connection, scope: Scope,
                     metric_defs: list[dict], personal: list[dict],
                     baseline_fn) -> list[dict]:
    """Shared assembly for the continuous-metric screens (Performance, Laning).

    Buckets the per-match `personal` rows per hero and overall, drops NULLs per
    metric so a sparse metric reads its own honest sample size, fetches the live
    population baseline via `baseline_fn(conn, scope, played)`, and hands each
    metric's value list to stats.mean_interval / mean_verdict through
    _metric_fields. Returns the canonical shape: the overall row first, then
    heroes A->Z, each with a `metrics` list in `metric_defs` order. The per-metric
    `games` is the non-null sample size; the row-level `games` is the match count
    for that scope. The only thing that differs between screens is which metrics
    and which baseline -- the bucketing and the stats wiring are identical, so
    they can never drift."""
    names = queries.hero_names(conn)
    images = queries.hero_images(conn)

    keys = [m["key"] for m in metric_defs]
    overall_vals: dict[str, list] = {k: [] for k in keys}
    per_hero_vals: dict[int, dict[str, list]] = {}
    overall_games = 0
    per_hero_games: dict[int, int] = {}
    for row in personal:
        hid = row["hero_id"]
        hv = per_hero_vals.setdefault(hid, {k: [] for k in keys})
        per_hero_games[hid] = per_hero_games.get(hid, 0) + 1
        overall_games += 1
        for k in keys:
            v = row[k]
            if v is not None:
                hv[k].append(v)
                overall_vals[k].append(v)

    played = sorted(per_hero_vals)
    baseline = baseline_fn(conn, scope, played)

    def _scope_row(scope_kind: str, hero_id: int | None, games: int,
                   vals: dict[str, list], base: dict | None) -> dict:
        metrics = []
        for mdef in metric_defs:
            k = mdef["key"]
            bmean = base.get(k) if base else None
            bn = base.get("n", 0) if base else 0
            field = _metric_fields(vals[k], bmean, bn, mdef["higher_is_better"])
            metrics.append({"key": k, "label": mdef["label"],
                            "higher_is_better": mdef["higher_is_better"], **field})
        return {
            "scope": scope_kind,
            "hero_id": hero_id,
            "hero_name": None if hero_id is None else names.get(hero_id, str(hero_id)),
            "hero_image_url": None if hero_id is None else images.get(hero_id),
            "games": games,
            "metrics": metrics,
        }

    rows = []
    if overall_games:
        rows.append(_scope_row("overall", None, overall_games,
                               overall_vals, baseline.get("overall")))
    for hid in sorted(played, key=lambda h: names.get(h, str(h)).lower()):
        rows.append(_scope_row("hero", hid, per_hero_games[hid],
                               per_hero_vals[hid], baseline.get(hid)))
    return rows


def performance(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Continuous-metric performance per hero and overall, each compared to a live
    population baseline (queries.baseline_performance). The continuous twin of
    matchups(): personal means + t-intervals + verdicts from stats/, assembled
    here. Net worth is per-minute; the rest are per-game averages.

    One object per scope row -- the overall row first, then heroes A->Z -- each
    carrying a `metrics` list in the canonical PERF_METRICS order. The per-metric
    `games` is the non-null sample size (so sparse metrics like healing read
    honestly), while the row-level `games` is the match count for that scope."""
    scope = _resolved(conn, scope)
    if scope is None:
        return []
    personal = queries.personal_performance(conn, scope)
    return _continuous_rows(conn, scope, queries.PERF_METRICS, personal,
                            queries.baseline_performance)


# ── Laning (early-game continuous metrics at the lane-end mark) ───────────────

def laning(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Early-game (laning) report: net worth, last hits, and denies at the
    lane-end mark (~10 min, stats.laning.LANE_END_S) per hero and overall, each vs
    the live population at the same mark (queries.baseline_laning). Identical
    assembly to performance() -- same per-hero/overall bucketing, the same
    t-interval + verdict machinery, the same honest fallbacks (a metric with no
    population data shows personal-only, never a comparison against nothing) --
    differing only in the metric set (LANING_METRICS, raw values at a fixed time,
    not per-minute) and the baseline source. Because lane outcomes drive games,
    this surfaces whether you win or lose lane against the field."""
    scope = _resolved(conn, scope)
    if scope is None:
        return []
    personal = queries.personal_laning(conn, scope)
    return _continuous_rows(conn, scope, queries.LANING_METRICS, personal,
                            queries.baseline_laning)


# ── Trends (performance over time) ───────────────────────────────────────────

TRENDS_MODES = ("rolling", "calendar")
TRENDS_GRANULARITIES = ("week", "month")


def _winrate_point(label: str, members: list[dict]) -> dict:
    """One win-rate point: rate + Wilson interval over a bucket's matches, with
    the honesty floor (a thin bucket reads not-enough-data)."""
    n = len(members)
    wins = sum(m["won"] for m in members)
    low, high = wilson_interval(wins, n)
    return {
        "label": label,
        "n": n,
        "value": _round(wins / n) if n else None,
        "ci_low": _round(low),
        "ci_high": _round(high),
        "enough_data": n >= VERDICT_FLOOR,
    }


def _metric_point(label: str, members: list[dict], key: str) -> dict:
    """One continuous-metric point: sample mean + t-interval over a bucket's
    non-null values, same honesty floor as win rate. NULLs are dropped, so a
    point's n is its own metric's sample size (sparse metrics read honestly)."""
    vals = [m[key] for m in members if m[key] is not None]
    n = len(vals)
    value = ci_low = ci_high = None
    if n:
        mean, low, high = mean_interval(vals, 0.95)
        value = _round(mean, _METRIC_DP)
        ci_low, ci_high = _round(_finite(low), _METRIC_DP), _round(_finite(high), _METRIC_DP)
    return {"label": label, "n": n, "value": value, "ci_low": ci_low,
            "ci_high": ci_high, "enough_data": n >= VERDICT_FLOOR}


def trends(conn: sqlite3.Connection, scope: Scope, *, mode: str = "rolling",
           granularity: str = "week",
           window_games: int = TRENDS_WINDOW_DEFAULT,
           hero_id: int | None = None) -> dict:
    """Performance over time for the scoped account: win rate plus every
    continuous metric (PERF_METRICS) as a chronological series, bucketed two ways
    the caller toggles -- a rolling window (moving average over the last
    `window_games` matches) or calendar buckets (per week / per month).

    The grouping is pure (stats.trends); this layer turns each bucket's matches
    into a point with the same Wilson / t-interval / VERDICT_FLOOR machinery the
    Performance screen uses, so thin windows read not-enough-data here exactly as
    they do there. Each metric carries one reference baseline drawn as the
    sparkline's line: win rate against the account's own overall rate over the
    series, continuous metrics against the live population mean at this scope
    (queries.baseline_performance, which excludes the account itself)."""
    if mode not in TRENDS_MODES:
        mode = "rolling"
    if granularity not in TRENDS_GRANULARITIES:
        granularity = "week"
    if window_games < 1:
        window_games = TRENDS_WINDOW_DEFAULT

    empty = {"mode": mode, "granularity": granularity,
             "window_games": window_games, "metrics": []}
    scope = _resolved(conn, scope)
    if scope is None:
        return empty

    series = queries.performance_series(conn, scope, my_hero_id=hero_id)
    if not series:
        return empty

    if mode == "calendar":
        buckets = bucket_by_calendar(series, granularity)
    else:
        buckets = rolling_windows(series, window_games)

    played = sorted({r["hero_id"] for r in series})
    baseline = queries.baseline_performance(conn, scope, played)
    base_overall = baseline.get("overall") or {}

    total_wins = sum(r["won"] for r in series)
    overall_rate = total_wins / len(series)

    metrics = [{
        "key": "win_rate", "label": "Win rate", "higher_is_better": True,
        "baseline": _round(overall_rate),
        "points": [_winrate_point(b["label"], b["items"]) for b in buckets],
    }]
    for mdef in queries.PERF_METRICS:
        k = mdef["key"]
        bmean = base_overall.get(k)
        metrics.append({
            "key": k, "label": mdef["label"],
            "higher_is_better": mdef["higher_is_better"],
            "baseline": _round(bmean, _METRIC_DP) if bmean is not None else None,
            "points": [_metric_point(b["label"], b["items"], k) for b in buckets],
        })

    return {"mode": mode, "granularity": granularity,
            "window_games": window_games, "metrics": metrics}


# ── Improvement digest ───────────────────────────────────────────────────────

def _played_hero_ids(conn: sqlite3.Connection, scope: Scope) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT hero_id FROM match_players mp"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ?",
        (scope.account_id, scope.game_mode),
    ).fetchall()
    return [r["hero_id"] for r in rows]


def _raw_delta(row: dict) -> float | None:
    if row["winrate"] is None or row["global_rate"] is None:
        return None
    return row["winrate"] - row["global_rate"]


def improvement(conn: sqlite3.Connection, scope: Scope,
                hero_id: int | None = None) -> dict:
    """A short ranked digest across matchups and items: confirmed weaknesses,
    confirmed strengths, and a watch list of large-but-unconfirmed deltas.
    An unconfirmed delta never appears outside the watch list (scenario 5).

    When hero_id is given, the whole digest is scoped to that one hero: only its
    matchups and item rows feed the digest. When it is None, the input is the
    across-all-heroes set (matchups aggregated over every played hero, item rows
    from each played hero)."""
    empty = {"confirmed_weaknesses": [], "confirmed_strengths": [], "watch_list": []}
    resolved = _resolved(conn, scope)
    if resolved is None:
        return empty

    entries = [dict(r, kind="matchup", subject=r["enemy_hero_name"])
               for r in matchups(conn, resolved, hero_id=hero_id)]
    item_hero_ids = [hero_id] if hero_id is not None else _played_hero_ids(conn, resolved)
    for hid in item_hero_ids:
        entries += [dict(r, kind="item", subject=r["item_name"], hero_id=hid)
                    for r in items(conn, resolved, hid)]

    # matchups()/items() now return thin rows for the tables; the digest keeps
    # min_games as its own gate so "lower Min games to see softer signals" holds.
    entries = [e for e in entries if e["games"] >= resolved.min_games]

    weaknesses, strengths, watch = [], [], []
    for e in entries:
        v = e["verdict"]
        if v == VERDICT_CLEAR_WEAKNESS:
            weaknesses.append(e)
        elif v == VERDICT_CLEAR_STRENGTH:
            strengths.append(e)
        elif v in (VERDICT_LEANING_WEAKNESS, VERDICT_LEANING_STRENGTH):
            watch.append(e)        # a softer signal: real but not yet confirmed

    weaknesses.sort(key=lambda e: e["delta"])               # most negative first
    strengths.sort(key=lambda e: -e["delta"])               # most positive first
    watch.sort(key=lambda e: -abs(_raw_delta(e) or 0.0))    # biggest raw gap first
    return {"confirmed_weaknesses": weaknesses,
            "confirmed_strengths": strengths,
            "watch_list": watch}


# ── Tilt (session-index and loss-streak performance) ─────────────────────────

def _index_label(bucket: dict) -> str:
    return f"{bucket['index']}+" if bucket["capped"] else str(bucket["index"])


def _streak_label(bucket: dict) -> str:
    n = bucket["streak"]
    noun = "loss" if (n == 1 and not bucket["capped"]) else "losses"
    return f"{n}+ {noun}" if bucket["capped"] else f"{n} {noun}"


def tilt(conn: sqlite3.Connection, scope: Scope) -> dict:
    """Performance by game-number-within-session and by preceding-loss-streak.

    Sessions are inferred from match-time gaps (stats.sessions); each bucket is
    compared to the account's OWN overall in-scope win rate -- "you vs your usual
    self" -- so a verdict means a real departure from your baseline, not from the
    global population. Thin buckets fall under the verdict floor and read as
    not_enough_data, exactly like every other screen."""
    empty = {
        "by_session_index": [], "by_loss_streak": [],
        "overall": {"games": 0, "wins": 0, "winrate": None},
        "sessions": 0, "session_gap_hours": SESSION_GAP_S / 3600,
    }
    scope = _resolved(conn, scope)
    if scope is None:
        return empty

    results = queries.account_results(conn, scope)
    sessions = group_sessions(results)
    overall_games = len(results)
    overall_wins = sum(r["won"] for r in results)

    def _row(bucket: dict, label: str) -> dict:
        row = {**bucket, "label": label}
        row.update(_stat_fields(bucket["wins"], bucket["games"],
                                overall_wins, overall_games))
        return row

    return {
        "by_session_index": [_row(b, _index_label(b))
                             for b in by_session_index(sessions)],
        "by_loss_streak": [_row(b, _streak_label(b))
                           for b in by_loss_streak(sessions)],
        "overall": {
            "games": overall_games, "wins": overall_wins,
            "winrate": _round(overall_wins / overall_games) if overall_games else None,
        },
        "sessions": len(sessions),
        "session_gap_hours": SESSION_GAP_S / 3600,
    }


# ── Recurring players (teammates you win with, opponents you beat) ───────────

def recurring_players(conn: sqlite3.Connection, scope: Scope,
                      hero_id: int | None = None) -> dict:
    """Other real players who keep sharing the account's matches, split into
    recurring teammates (your win rate WITH them) and opponents (your win rate
    AGAINST them). Like tilt, each is judged against the account's OWN win rate
    over the same match set -- overall, or on `hero_id` when the hero filter is
    set -- so a verdict means you do better/worse with (or against) that player
    than your usual self. Co-players below stats.recurring.MIN_CO_OCCURRENCE
    shared games are dropped; thin survivors fall under the verdict floor and
    read not_enough_data. display_name is resolved (manual label > Steam persona >
    bare account id) so even untracked co-players surface by their best name."""
    empty = {
        "teammates": [], "opponents": [],
        "overall": {"games": 0, "wins": 0, "winrate": None},
        "min_co_occurrence": MIN_CO_OCCURRENCE, "hero_id": hero_id,
    }
    scope = _resolved(conn, scope)
    if scope is None:
        return empty

    # Self-baseline stays scope-wide even under in_lane: in_lane changes only which
    # co-players count inside each match, not which matches I played, so this is
    # judged against my overall win rate over the same set. Don't lane-filter it.
    results = queries.account_results(conn, scope, my_hero_id=hero_id)
    overall_games = len(results)
    overall_wins = sum(r["won"] for r in results)

    split = split_recurring(queries.recurring_co_players(conn, scope, my_hero_id=hero_id))
    names = queries.resolve_names(
        conn, [c["account_id"] for c in split["teammates"] + split["opponents"]])

    def _row(co: dict) -> dict:
        row = {
            "account_id": co["account_id"],
            "display_name": names.get(co["account_id"]),
            "games": co["games"],
            "wins": co["wins"],
        }
        row.update(_stat_fields(co["wins"], co["games"], overall_wins, overall_games))
        return row

    return {
        "teammates": [_row(c) for c in split["teammates"]],
        "opponents": [_row(c) for c in split["opponents"]],
        "overall": {
            "games": overall_games, "wins": overall_wins,
            "winrate": _round(overall_wins / overall_games) if overall_games else None,
        },
        "min_co_occurrence": MIN_CO_OCCURRENCE,
        "hero_id": hero_id,
    }


# ── Heroes the account plays (the "my hero" picker) ──────────────────────────

def played_heroes(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Heroes the scoped account has played, each with name + icon URL, sorted
    by name. Powers the matchups hero-perspective selector."""
    resolved = _resolved(conn, scope)
    if resolved is None:
        return []
    names = queries.hero_names(conn)
    images = queries.hero_images(conn)
    heroes = [
        {"hero_id": hid, "name": names.get(hid, str(hid)), "image_url": images.get(hid)}
        for hid in _played_hero_ids(conn, resolved)
    ]
    heroes.sort(key=lambda h: h["name"])
    return heroes


# ── Rank tiers (for the rank-range selector) ─────────────────────────────────

_RANK_ART_BASE = "https://assets-bucket.deadlock-api.com/assets-api-res/images/ranks"


def _rank_badge_url(tier: int) -> str:
    """Derive the tier badge-art URL from the assets CDN layout. The URL is a
    pure function of the tier, so we derive it rather than storing it."""
    return f"{_RANK_ART_BASE}/rank{tier}/badge_lg.png"


def resolve_badge(badge: int | None, rank_tiers: list[dict]) -> dict | None:
    """Map a numeric badge (tier*10 + subtier, 0..116; api-findings spike 12) to a
    display rank: the tier name + accent color from the ranks table, the subtier,
    and the derived tier badge art. None for a missing badge, so "current rank" is
    simply absent rather than faked. rank_tiers is queries.list_ranks(conn)."""
    if badge is None:
        return None
    tier = badge // 10
    by_tier = {r["tier"]: r for r in rank_tiers}
    row = by_tier.get(tier)
    return {
        "badge": badge,
        "tier": tier,
        "subtier": badge % 10,
        "name": row["name"] if row else None,
        "color": row["color"] if row else None,
        "badge_url": _rank_badge_url(tier),
    }


def ranks(conn: sqlite3.Connection) -> list[dict]:
    """Rank tiers (name + color from the DB) plus a derived badge-art URL. One
    entry per tier: the analytics badge filter only partitions cleanly at decade
    (tier) granularity, so the rank selector is tier-granular (api-findings
    finding 6)."""
    return [
        {
            "tier": r["tier"],
            "name": r["name"],
            "color": r["color"],
            "badge_url": _rank_badge_url(r["tier"]),
        }
        for r in queries.list_ranks(conn)
    ]


# ── Tracked accounts (the account switcher) ──────────────────────────────────

def accounts(conn: sqlite3.Connection, user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """The user's accounts for their account switcher. display_name is resolved
    (manual label > Steam persona > bare account id) so the switcher reads the same
    names as the rest of the app; is_self is coerced to a bool so the JSON reads
    cleanly (mirrors overview's bool(won))."""
    rows = queries.list_tracked_accounts(conn, user_id)
    names = queries.resolve_names(conn, [a["account_id"] for a in rows], user_id)
    return [{"account_id": a["account_id"], "display_name": names[a["account_id"]],
             "is_self": bool(a["is_self"])}
            for a in rows]


def me(conn: sqlite3.Connection, user_id: int) -> dict:
    """The viewer's identity block for /api/auth/me: their user id, self account,
    and that account's resolved display name (None when they have no self account)."""
    self_id = queries.resolve_self_account_id(conn, user_id)
    name = (queries.resolve_names(conn, [self_id], user_id)[self_id]
            if self_id is not None else None)
    return {"user_id": user_id, "account_id": self_id, "display_name": name}


def add_account(conn: sqlite3.Connection, identifier: int | str,
                display_name: str | None = None,
                *, user_id: int = DEFAULT_USER_ID) -> dict:
    """Import a tracked account for a user and return its stored row (the importer
    endpoint).

    Reuses the CLI's ingest.accounts.add_account, which idempotently inserts the
    tracked_accounts + sync_state rows and links the account to user_id. That insert
    IS the enqueue: the worker's discovery loop reads every tracked account each
    cycle, so nothing is fetched here -- the request returns at once and the worker
    does the ingestion later.

    is_self stays False on purpose: importing/claiming an account is not the same as
    flagging it as the user's self account. Re-adding an existing account returns its
    stored row unchanged (INSERT OR IGNORE), so the response is always the truth on
    disk.

    Raises ValueError (from to_account_id) on an unparseable identifier; the
    handler turns that into a 400.
    """
    account_id = ingest_accounts.add_account(conn, identifier,
                                             display_name=display_name, user_id=user_id)
    # account_labels is the single source of manual names, so an add-with-name also
    # writes a label -- otherwise the resolver (which no longer reads
    # tracked_accounts.display_name) wouldn't surface the name the importer just set.
    if display_name and display_name.strip():
        set_account_name(conn, account_id, display_name.strip(), user_id=user_id)
    stored = queries.get_tracked_account(conn, account_id, user_id)
    return {**stored, "is_self": bool(stored["is_self"])}


def set_account_name(conn: sqlite3.Connection, account_id: int, display_name: str,
                     *, user_id: int = DEFAULT_USER_ID, now=utcnow) -> dict:
    """Upsert a manual label for an account (the namer). Works for ANY account_id,
    tracked or not -- co-players and opponents are mostly untracked, and naming
    them is the whole point. user_id scopes the label to one user (the local/dev
    user today). Returns {account_id, display_name} where display_name is the
    now-effective resolved name."""
    conn.execute(
        "INSERT INTO account_labels(user_id, account_id, display_name, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(user_id, account_id) DO UPDATE SET"
        "   display_name = excluded.display_name, updated_at = excluded.updated_at",
        (user_id, account_id, display_name, now().isoformat()),
    )
    conn.commit()
    return {"account_id": account_id,
            "display_name": queries.resolve_names(conn, [account_id], user_id)[account_id]}


def clear_account_name(conn: sqlite3.Connection, account_id: int,
                       *, user_id: int = DEFAULT_USER_ID) -> dict:
    """Clear an account's manual label, reverting it to its Steam persona then its
    bare id. Idempotent: clearing a label that isn't there is a no-op, not a 404.
    Returns {account_id, display_name} with the reverted resolved name so the UI
    can show what it fell back to."""
    conn.execute(
        "DELETE FROM account_labels WHERE user_id = ? AND account_id = ?",
        (user_id, account_id),
    )
    conn.commit()
    return {"account_id": account_id,
            "display_name": queries.resolve_names(conn, [account_id], user_id)[account_id]}


# ── Overview / sync / eras ───────────────────────────────────────────────────

def sync_status(conn: sqlite3.Connection) -> dict:
    counts = queries.queue_counts(conn)
    depth = counts.get("pending", 0) + counts.get("failed", 0)
    status = {
        "queue": counts,
        "queue_depth": depth,
        "fetched": counts.get("fetched", 0),
        "unavailable": counts.get("unavailable", 0),
        "last_discovery_at": queries.last_discovery_at(conn),
        "last_maintenance_at": queries.last_maintenance_at(conn),
        "pending_era_candidates": queries.pending_candidate_count(conn),
    }
    if not counts:
        status["message"] = "Nothing queued yet. Add an account and run the worker."
    return status


def overview(conn: sqlite3.Connection, scope: Scope) -> dict:
    sync = sync_status(conn)
    resolved = _resolved(conn, scope)
    if resolved is None:
        return {
            "account_id": None, "mmr_series": [], "current_rank": None,
            "last_matches": [], "sync": sync,
            "message": "No tracked account yet. Add one with:"
                       " python -m ingest add-account <id> --self",
        }

    names = queries.hero_names(conn)
    images = queries.hero_images(conn)
    recent = queries.last_matches(conn, resolved.account_id)
    for m in recent:
        m["hero_name"] = names.get(m["hero_id"], str(m["hero_id"]))
        m["image_url"] = images.get(m["hero_id"])
        m["won"] = bool(m["won"])
    # The series is ordered ascending by recorded_at, so its last point is the
    # current rank -- which the Batch MMR endpoint confirmed equals the live rank
    # (api-findings spike 12), so no extra request is needed.
    series = queries.mmr_series(conn, resolved.account_id)
    current_rank = resolve_badge(series[-1]["badge"], queries.list_ranks(conn)) if series else None
    result = {
        "account_id": resolved.account_id,
        "mmr_series": series,
        "current_rank": current_rank,
        "last_matches": recent,
        "sync": sync,
    }
    if not recent:
        result["message"] = "No matches ingested yet — the worker may still be syncing."
    return result


def match_detail(conn: sqlite3.Connection, match_id: int,
                 account_id: int | None = None) -> dict | None:
    """One match, parsed for display: the 12-player roster, the perspective
    account's purchases, and the whole-match kill/death feed. None if the match
    isn't stored (the endpoint turns that into a 404).

    `account_id` is the "you" perspective -- carried from whichever Overview the
    click came from -- defaulting to the tracked self account. It decides which
    player is highlighted and whose purchases are shown, but never which match.
    The roster and feed are parsed from raw_json (the only place player_slot
    lives); names/images come from the lookup tables, mirroring overview()."""
    row = queries.match_core(conn, match_id)
    if row is None:
        return None

    meta = json.loads(row["raw_json"]) if row["raw_json"] else {}
    parsed = detail.parse_detail(meta)
    perspective = account_id if account_id is not None else queries.resolve_self_account_id(conn)

    names = queries.hero_names(conn)
    images = queries.hero_images(conn)
    account_names = queries.resolve_names(
        conn, [p["account_id"] for p in parsed["players"]])

    players = []
    for p in parsed["players"]:
        players.append({
            **p,
            "hero_name": names.get(p["hero_id"], str(p["hero_id"])),
            "image_url": images.get(p["hero_id"]),
            "display_name": account_names.get(p["account_id"], str(p["account_id"])),
            "is_you": p["account_id"] == perspective,
        })

    deaths = []
    for d in parsed["deaths"]:
        killer_id = d["killer_hero_id"]
        deaths.append({
            **d,
            "killer_hero_name": names.get(killer_id) if killer_id is not None else None,
            "killer_image_url": images.get(killer_id) if killer_id is not None else None,
            "victim_hero_name": names.get(d["victim_hero_id"], str(d["victim_hero_id"])),
            "victim_image_url": images.get(d["victim_hero_id"]),
            "killer_is_you": d["killer_slot"] is not None
                             and _slot_account(parsed["players"], d["killer_slot"]) == perspective,
            "victim_is_you": _slot_account(parsed["players"], d["victim_slot"]) == perspective,
        })

    # Purchases are keyed on player_slot now, so resolve the perspective account
    # to its slot in this match's roster (a tracked account is never anonymized,
    # so this is unambiguous). None means the perspective didn't play this match.
    perspective_slot = next(
        (p["player_slot"] for p in parsed["players"] if p["account_id"] == perspective),
        None,
    )
    item_names = queries.item_names(conn)
    item_images = queries.item_images(conn)
    purchases = []
    if perspective_slot is not None:
        for b in queries.match_purchases(conn, match_id, perspective_slot):
            purchases.append({
                **b,
                "item_name": item_names.get(b["item_id"], str(b["item_id"])),
                "item_image_url": item_images.get(b["item_id"]),
            })

    # Per-match kill trades vs each opponent (design note 2): raw counts in both
    # directions, attributed by slot off kill_events so an anonymized opponent
    # (account_id = 0) is still counted, with its hero surfaced for labelling.
    # Enemy team only -- kills are cross-team, so a teammate row would be 0/0.
    # No verdict here: a kill-trade verdict needs a baseline and lives in stats/.
    trades = []
    if perspective_slot is not None:
        perspective_team = next(
            (p["team"] for p in players if p["player_slot"] == perspective_slot), None)
        counts = {(t["killer_slot"], t["victim_slot"]): t["n"]
                  for t in queries.match_kill_trades(conn, match_id)}
        for p in players:
            if p["team"] == perspective_team:        # skips the perspective and its team
                continue
            slot = p["player_slot"]
            trades.append({
                "player_slot": slot,
                "account_id": p["account_id"],
                "display_name": p["display_name"],
                "hero_id": p["hero_id"],
                "hero_name": p["hero_name"],
                "image_url": p["image_url"],
                "team": p["team"],
                "kills_by_them_on_you": counts.get((slot, perspective_slot), 0),
                "kills_by_you_on_them": counts.get((perspective_slot, slot), 0),
            })
        trades.sort(key=lambda t: t["player_slot"])

    return {
        "match_id": row["match_id"],
        "start_time": row["start_time"],
        "duration_s": row["duration_s"],
        "game_mode": row["game_mode"],
        "winning_team": row["winning_team"],
        "average_badge_team0": row["average_badge_team0"],
        "average_badge_team1": row["average_badge_team1"],
        "account_id": perspective,
        "players": players,
        "purchases": purchases,
        "deaths": deaths,
        "trades": trades,
    }


def _slot_account(players: list[dict], slot: int | None) -> int | None:
    """The account_id at a given player_slot, for the "is this you?" checks."""
    if slot is None:
        return None
    for p in players:
        if p["player_slot"] == slot:
            return p["account_id"]
    return None


def eras(conn: sqlite3.Connection) -> dict:
    era_rows = queries.list_eras(conn)
    result = {"eras": era_rows, "pending_candidates": queries.list_pending_candidates(conn)}
    if not era_rows:
        result["message"] = "No eras defined yet."
    return result


# ── Writes (admin era management) ────────────────────────────────────────────

def rebin_eras(conn: sqlite3.Connection) -> int:
    """Recompute every match's era_id from its start_time against the current
    era boundaries. A single UPDATE, no re-ingestion: redrawing a boundary
    re-scopes every era-scoped stat (scenario 3). Returns rows updated."""
    cur = conn.execute(
        "UPDATE matches SET era_id = ("
        "  SELECT e.era_id FROM patch_eras e"
        "  WHERE e.started_at <= matches.start_time"
        "  ORDER BY e.started_at DESC LIMIT 1)"
    )
    conn.commit()
    return cur.rowcount


def confirm_candidate(conn: sqlite3.Connection, candidate_id: int) -> dict:
    """Confirm an era candidate: create the era boundary (which closes the prior
    era, since an era runs until the next one's start), mark it confirmed, and
    re-bin matches so the new era has correctly-scoped stats from day one."""
    cand = queries.get_candidate(conn, candidate_id)
    if cand is None:
        return {"ok": False, "error": "candidate not found"}

    label = cand["post_title"] or f"Era from candidate {candidate_id}"
    conn.execute(
        "INSERT OR IGNORE INTO patch_eras(label, started_at) VALUES (?, ?)",
        (label, cand["posted_at"]),
    )
    era_row = conn.execute(
        "SELECT era_id FROM patch_eras WHERE started_at = ?", (cand["posted_at"],)
    ).fetchone()
    conn.execute(
        "UPDATE era_candidates SET status = 'confirmed' WHERE candidate_id = ?",
        (candidate_id,),
    )
    conn.commit()
    rebinned = rebin_eras(conn)
    return {"ok": True, "era_id": era_row["era_id"] if era_row else None,
            "rebinned": rebinned}


def dismiss_candidate(conn: sqlite3.Connection, candidate_id: int) -> dict:
    cand = queries.get_candidate(conn, candidate_id)
    if cand is None:
        return {"ok": False, "error": "candidate not found"}
    conn.execute(
        "UPDATE era_candidates SET status = 'dismissed' WHERE candidate_id = ?",
        (candidate_id,),
    )
    conn.commit()
    return {"ok": True}
