"""Loop 3: maintenance (nightly housekeeping).

- Second chances: yesterday's 'unavailable' may be fetchable today, because
  Valve's unlock throttle releases old match reports in batches. Reviving
  stale unavailables is the whole automation story for old match reports.
- Refresh global baselines (decade brackets) into a single evolving snapshot,
  staggered by era mutability (open era nightly, closed eras weekly/monthly).
  NEVER omit the date params: the analytics endpoint defaults to a trailing
  30-day window, which would silently store recent-meta numbers under an older
  era's label (api-findings contradiction 7).
- Refresh heroes/items from the assets API.
- Detect new era candidates from Steam News.
- Log a one-line summary.
"""
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from ingest.client import BASE_URL, archive_response
from ingest.eras import detect_era_candidates
from ingest.util import iso_to_unix, utcnow
from tracker.reference import load_heroes, load_items, load_ranks

log = logging.getLogger(__name__)

REQUEUE_WINDOW_S = 24 * 3600
ALL_TIME_ERA_ID = 0           # sentinel: NULL breaks SQLite composite-PK dedup

# Gapless decade brackets tiling 0..116 (12). Each baseline call fetches one
# bracket; the query layer (api/queries.py) re-sums the brackets a scope
# contains. Decades are the FINEST CLEAN partition the analytics badge filter
# supports: narrower brackets drop matches whose fractional team-average badge
# falls in the gaps between integer edges (gate spike 09 + width sweep 11 ->
# api-findings finding 6; width-5 already leaks ~4%, width-1 ~15%). Must stay
# aligned with api.scope.snap_badge_range, which snaps a requested range to these
# same decade edges so containment never splits one.
DECADE_BRACKETS = [(0, 9)] + [(t * 10, t * 10 + 9) for t in range(1, 11)] + [(110, 116)]

# Staggered-refresh cadence (cache-by-mutability): the open era changes every
# night, a recently-closed era barely changes (only late-arriving matches), an
# old closed era and the all-time sentinel are effectively immutable. Re-fetching
# every era's brackets nightly is wasteful, so we pull rarely-changing eras
# rarely. These are the staleness thresholds.
DAY_S = 24 * 3600
WEEKLY_S = 7 * DAY_S
MONTHLY_S = 30 * DAY_S
RECENTLY_CLOSED_S = 30 * DAY_S    # a closed era younger than this refreshes weekly


def revive_unavailable(conn, *, now=utcnow) -> int:
    """Re-queue 'unavailable' matches whose last attempt is older than 24h."""
    cutoff = (now() - timedelta(seconds=REQUEUE_WINDOW_S)).isoformat()
    cursor = conn.execute(
        "UPDATE fetch_queue SET status = 'pending', attempts = 0, next_retry_at = NULL"
        " WHERE status = 'unavailable' AND last_attempt_at < ?",
        (cutoff,),
    )
    conn.commit()
    if cursor.rowcount:
        log.info("maintenance: revived %d unavailable match(es)", cursor.rowcount)
    return cursor.rowcount


@dataclass(frozen=True)
class EraSpan:
    """One baseline time window. is_open marks the live era (ends at now, so it
    refreshes every run); closed_at is the unix time the era ended (the next
    era's start), None for the open era and the all-time sentinel."""
    era_id: int
    min_unix: int
    max_unix: int
    is_open: bool
    closed_at: int | None


def _era_spans(conn, now_unix: int) -> list[EraSpan]:
    """One EraSpan per patch era (each bounded by the next era's start; the last
    one is open and ends at now), plus the all-time sentinel span."""
    eras = conn.execute(
        "SELECT era_id, started_at FROM patch_eras ORDER BY started_at"
    ).fetchall()
    spans = []
    for i, era in enumerate(eras):
        min_unix = iso_to_unix(era["started_at"])
        is_open = i + 1 == len(eras)
        if is_open:
            spans.append(EraSpan(era["era_id"], min_unix, now_unix, True, None))
        else:
            closed_at = iso_to_unix(eras[i + 1]["started_at"])
            spans.append(EraSpan(era["era_id"], min_unix, closed_at, False, closed_at))
    spans.append(EraSpan(ALL_TIME_ERA_ID, 0, now_unix, False, None))
    return spans


def _refresh_due(span: EraSpan, now_unix: int, last_refreshed_at: str | None) -> bool:
    """Whether a span's baselines are stale enough to re-fetch (see the cadence
    constants). Never-fetched spans are always due, which is what makes the first
    per-badge run rebuild every era."""
    if last_refreshed_at is None:
        return True
    if span.is_open:
        return True
    age_s = now_unix - iso_to_unix(last_refreshed_at)
    if span.era_id == ALL_TIME_ERA_ID:
        return age_s >= MONTHLY_S
    recently_closed = span.closed_at is not None and now_unix - span.closed_at <= RECENTLY_CLOSED_S
    return age_s >= (WEEKLY_S if recently_closed else MONTHLY_S)


def _counter_url(min_unix: int, max_unix: int, badge_min: int, badge_max: int,
                 same_lane: bool = False) -> str:
    # game_mode=normal: the analytics endpoints take the STRING variant
    # (normal/street_brawl/...), NOT the numeric 1 that match metadata uses --
    # game_mode=1 returns HTTP 400 "unknown variant `1`" (api-findings
    # contradiction 8). Normal keeps baselines comparable to personal stats;
    # Street Brawl must never be lumped in.
    #
    # same_lane_filter=true restricts to lane-opponent pairs, giving the
    # laning-phase baseline the in-lane view compares against.
    url = (
        f"{BASE_URL}/v1/analytics/hero-counter-stats"
        f"?min_unix_timestamp={min_unix}&max_unix_timestamp={max_unix}"
        f"&min_average_badge={badge_min}&max_average_badge={badge_max}"
        f"&game_mode=normal"
    )
    if same_lane:
        url += "&same_lane_filter=true"
    return url


def _item_stats_url(min_unix: int, max_unix: int, badge_min: int, badge_max: int) -> str:
    # bucket=hero returns per-hero-per-item rows in one call; the `bucket`
    # field carries the hero_id (api-findings, verified 2026-06-13). bucket=hero
    # honors min/max_average_badge (gate spike 08), so we bracket it too.
    # game_mode=normal (string variant) for the same reason as _counter_url.
    return (
        f"{BASE_URL}/v1/analytics/item-stats"
        f"?bucket=hero&min_unix_timestamp={min_unix}&max_unix_timestamp={max_unix}"
        f"&min_average_badge={badge_min}&max_average_badge={badge_max}"
        f"&game_mode=normal"
    )


def _last_refreshed_by_era(conn) -> dict[int, str]:
    """era_id -> ISO timestamp of its last real fetch (baseline_refresh_state)."""
    return {r["era_id"]: r["last_refreshed_at"]
            for r in conn.execute(
                "SELECT era_id, last_refreshed_at FROM baseline_refresh_state")}


def _latest_snapshot_id(conn) -> int | None:
    row = conn.execute("SELECT MAX(snapshot_id) AS s FROM baseline_snapshots").fetchone()
    return row["s"] if row else None


def refresh_baselines(conn, client, *, now=utcnow) -> int:
    """Refresh global baselines into a SINGLE evolving snapshot, staggered by era
    mutability. Each run re-fetches only the eras that are due (the open era every
    run; closed eras weekly/monthly; the all-time sentinel monthly) and replaces
    just those eras' rows in place, so the latest snapshot stays complete for the
    read layer (which reads MAX(snapshot_id)). Baselines are decade-bracketed and
    Normal-only (game_mode=normal) so they line up with personal stats. Returns
    the snapshot_id."""
    fetched_at = now().isoformat()
    now_unix = iso_to_unix(fetched_at)

    # First staggered run (no refresh state yet): start a fresh snapshot, and
    # because the state is empty every era is due, so this run rebuilds them all.
    # Later runs evolve that same snapshot, replacing only the due eras.
    refreshed = _last_refreshed_by_era(conn)
    snapshot_id = _latest_snapshot_id(conn) if refreshed else None
    if snapshot_id is None:
        cursor = conn.execute(
            "INSERT INTO baseline_snapshots(fetched_at, notes) VALUES (?, ?)",
            (fetched_at, "staggered baselines"),
        )
        snapshot_id = cursor.lastrowid

    for span in _era_spans(conn, now_unix):
        if not _refresh_due(span, now_unix, refreshed.get(span.era_id)):
            log.info("baselines: era %s not due yet, skipping", span.era_id)
            continue
        # Replace this era's rows in the evolving snapshot, then re-fetch fresh.
        conn.execute("DELETE FROM baseline_hero_matchups WHERE snapshot_id = ? AND era_id = ?",
                     (snapshot_id, span.era_id))
        conn.execute("DELETE FROM baseline_hero_item_stats WHERE snapshot_id = ? AND era_id = ?",
                     (snapshot_id, span.era_id))
        _fetch_era_baselines(conn, client, snapshot_id, span, fetched_at)
        conn.execute(
            "INSERT INTO baseline_refresh_state(era_id, last_refreshed_at) VALUES (?, ?)"
            " ON CONFLICT(era_id) DO UPDATE SET last_refreshed_at = excluded.last_refreshed_at",
            (span.era_id, fetched_at),
        )
        conn.commit()   # commit per era so a mid-run failure keeps finished eras
        log.info("baselines: era %s refreshed into snapshot %d", span.era_id, snapshot_id)
    return snapshot_id


def _parse_baseline_rows(body: str, url: str) -> list:
    """Parse an analytics response body into a list of rows, tolerating a 200
    that carries no usable JSON. HTTP 200 only says the request succeeded, not
    that the body is a JSON array: the analytics endpoints sometimes answer 200
    with an empty or truncated body, and json.loads("") raises JSONDecodeError.
    Treat any empty/blank/unparseable body as 'no rows' (warn + skip) so one bad
    bracket can't abort the whole nightly refresh."""
    if not body or not body.strip():
        log.warning("baselines: empty 200 body from %s; skipping bracket", url)
        return []
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("baselines: malformed 200 body from %s (%s); skipping bracket", url, exc)
        return []


def _fetch_era_baselines(conn, client, snapshot_id: int, span: EraSpan,
                         fetched_at: str) -> None:
    """Fetch matchup + item baselines for one era span into the snapshot, one
    call per decade bracket per endpoint. Matchups are fetched twice per bracket
    -- overall (same_lane=0) and lane-opponents only (same_lane=1) -- so the
    in-lane view has a matching baseline. Empty brackets insert nothing."""
    for badge_min, badge_max in DECADE_BRACKETS:
        for same_lane in (0, 1):
            url = _counter_url(span.min_unix, span.max_unix, badge_min, badge_max,
                               same_lane=bool(same_lane))
            status, _headers, body = client.get(url)
            archive_response(conn, url, status, body, fetched_at)
            if status == 200:
                conn.executemany(
                    "INSERT INTO baseline_hero_matchups"
                    " (snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max,"
                    "  same_lane, wins, matches, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(snapshot_id, r["hero_id"], r["enemy_hero_id"], span.era_id,
                      badge_min, badge_max, same_lane, r["wins"], r["matches_played"], fetched_at)
                     for r in _parse_baseline_rows(body, url)],
                )
            else:
                log.warning("baselines: hero-counter-stats HTTP %s for era %s badge %d-%d "
                            "same_lane=%d", status, span.era_id, badge_min, badge_max, same_lane)

        # Item baselines: item-stats?bucket=hero; the `bucket` field is the hero_id.
        item_url = _item_stats_url(span.min_unix, span.max_unix, badge_min, badge_max)
        item_status, _h, item_body = client.get(item_url)
        archive_response(conn, item_url, item_status, item_body, fetched_at)
        if item_status == 200:
            conn.executemany(
                "INSERT INTO baseline_hero_item_stats"
                " (snapshot_id, hero_id, item_id, era_id, badge_min, badge_max,"
                "  wins, matches, avg_purchase_s, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(snapshot_id, r["bucket"], r["item_id"], span.era_id, badge_min, badge_max,
                  r["wins"], r["matches"], r.get("avg_buy_time_s"), fetched_at)
                 for r in _parse_baseline_rows(item_body, item_url)],
            )
        else:
            log.warning("baselines: item-stats HTTP %s for era %s badge %d-%d",
                        item_status, span.era_id, badge_min, badge_max)


def refresh_assets(conn, client, *, now=utcnow) -> None:
    """Reload heroes and items from the assets API (archive raw first)."""
    fetched_at = now().isoformat()
    for path, loader in (("/v1/assets/heroes", load_heroes),
                         ("/v1/assets/items", load_items),
                         ("/v1/assets/ranks", load_ranks)):
        url = f"{BASE_URL}{path}"
        status, _headers, body = client.get(url)
        archive_response(conn, url, status, body, fetched_at)
        if status == 200:
            loader(conn, json.loads(body), fetched_at)
        else:
            log.warning("assets: %s HTTP %s", path, status)


def _queue_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM fetch_queue GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def run_maintenance(conn, client, *, now=utcnow) -> dict:
    """The full nightly job. Returns a summary dict."""
    revived = revive_unavailable(conn, now=now)
    refresh_baselines(conn, client, now=now)
    refresh_assets(conn, client, now=now)
    candidates = detect_era_candidates(conn, client, now=now)

    counts = _queue_counts(conn)
    queue_depth = counts.get("pending", 0) + counts.get("failed", 0)
    conn.execute(
        "INSERT INTO worker_meta(key, value) VALUES('last_maintenance_at', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (now().isoformat(),),
    )
    conn.commit()

    summary = {
        "revived": revived,
        "candidates": candidates,
        "queue": counts,
        "queue_depth": queue_depth,
    }
    log.info(
        "maintenance summary: fetched %d, failed %d, unavailable %d, queue depth %d, "
        "revived %d, new era candidates %d",
        counts.get("fetched", 0), counts.get("failed", 0), counts.get("unavailable", 0),
        queue_depth, revived, candidates,
    )
    return summary
