"""Loop 3: maintenance (nightly housekeeping).

- Second chances: yesterday's 'unavailable' may be fetchable today, because
  Valve's unlock throttle releases old match reports in batches. Reviving
  stale unavailables is the whole automation story for old match reports.
- Refresh global baselines, one request per era plus an explicit all-time
  span. NEVER omit the date params: the analytics endpoint defaults to a
  trailing 30-day window, which would silently store recent-meta numbers
  under an older era's label (api-findings contradiction 7).
- Refresh heroes/items from the assets API.
- Detect new era candidates from Steam News.
- Log a one-line summary.
"""
import json
import logging
from datetime import timedelta

from ingest.client import BASE_URL, archive_response
from ingest.eras import detect_era_candidates
from ingest.util import iso_to_unix, utcnow
from tracker.reference import load_heroes, load_items

log = logging.getLogger(__name__)

REQUEUE_WINDOW_S = 24 * 3600
ALL_TIME_ERA_ID = 0           # sentinel: NULL breaks SQLite composite-PK dedup

# Gapless decade brackets tiling 0..116 (12). Each baseline call fetches one
# bracket; the query layer (api/queries.py) re-sums the brackets a scope
# contains. Must stay aligned with api.scope.snap_badge_range, which snaps a
# requested range to these same decade edges so containment never splits one.
DECADE_BRACKETS = [(0, 9)] + [(t * 10, t * 10 + 9) for t in range(1, 11)] + [(110, 116)]


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


def _era_spans(conn, now_unix: int) -> list[tuple[int, int, int]]:
    """(era_id, min_unix, max_unix) per era, each bounded by the next era's
    start (the open era ends at now), plus the all-time span."""
    eras = conn.execute(
        "SELECT era_id, started_at FROM patch_eras ORDER BY started_at"
    ).fetchall()
    spans = []
    for i, era in enumerate(eras):
        min_unix = iso_to_unix(era["started_at"])
        max_unix = iso_to_unix(eras[i + 1]["started_at"]) if i + 1 < len(eras) else now_unix
        spans.append((era["era_id"], min_unix, max_unix))
    spans.append((ALL_TIME_ERA_ID, 0, now_unix))
    return spans


def _counter_url(min_unix: int, max_unix: int, badge_min: int, badge_max: int) -> str:
    # game_mode=normal: the analytics endpoints take the STRING variant
    # (normal/street_brawl/...), NOT the numeric 1 that match metadata uses --
    # game_mode=1 returns HTTP 400 "unknown variant `1`" (api-findings
    # contradiction 8). Normal keeps baselines comparable to personal stats;
    # Street Brawl must never be lumped in.
    return (
        f"{BASE_URL}/v1/analytics/hero-counter-stats"
        f"?min_unix_timestamp={min_unix}&max_unix_timestamp={max_unix}"
        f"&min_average_badge={badge_min}&max_average_badge={badge_max}"
        f"&game_mode=normal"
    )


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


def refresh_baselines(conn, client, *, now=utcnow) -> int:
    """Fetch matchup + item baselines for every era span into a fresh snapshot,
    one call per (era span, decade bracket) per endpoint. Baselines are
    Normal-only (game_mode=normal) so they line up with personal stats, which
    also default to Normal; Street Brawl is never mixed in. Old snapshots are
    kept for time-travel debugging. Returns snapshot_id."""
    fetched_at = now().isoformat()
    now_unix = iso_to_unix(fetched_at)
    cursor = conn.execute(
        "INSERT INTO baseline_snapshots(fetched_at, notes) VALUES (?, ?)",
        (fetched_at, "nightly matchup baselines"),
    )
    snapshot_id = cursor.lastrowid

    # The decade brackets tile 0..116, so the full-range baseline (a scope that
    # contains every bracket) covers RATED matches only: matches with an unknown
    # (NULL) average badge -- a ~4% early-access tail with no rank to compare --
    # fall outside every bracket and are excluded by design. No all-ranks row.
    for era_id, min_unix, max_unix in _era_spans(conn, now_unix):
        for badge_min, badge_max in DECADE_BRACKETS:
            # Matchup baselines: hero-counter-stats, one call per bracket.
            url = _counter_url(min_unix, max_unix, badge_min, badge_max)
            status, _headers, body = client.get(url)
            archive_response(conn, url, status, body, fetched_at)
            if status == 200:
                matchup_rows = [
                    (snapshot_id, r["hero_id"], r["enemy_hero_id"], era_id,
                     badge_min, badge_max, r["wins"], r["matches_played"], fetched_at)
                    for r in json.loads(body)
                ]
                conn.executemany(
                    "INSERT INTO baseline_hero_matchups"
                    " (snapshot_id, hero_id, enemy_hero_id, era_id, badge_min, badge_max,"
                    "  wins, matches, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    matchup_rows,
                )
            else:
                log.warning("baselines: hero-counter-stats HTTP %s for era %s badge %d-%d",
                            status, era_id, badge_min, badge_max)

            # Item baselines: item-stats?bucket=hero, also one call per bracket;
            # the `bucket` field is the hero_id, `wins+losses` reconcile to matches.
            item_url = _item_stats_url(min_unix, max_unix, badge_min, badge_max)
            item_status, _h, item_body = client.get(item_url)
            archive_response(conn, item_url, item_status, item_body, fetched_at)
            if item_status == 200:
                item_rows = [
                    (snapshot_id, r["bucket"], r["item_id"], era_id,
                     badge_min, badge_max, r["wins"], r["matches"],
                     r.get("avg_buy_time_s"), fetched_at)
                    for r in json.loads(item_body)
                ]
                conn.executemany(
                    "INSERT INTO baseline_hero_item_stats"
                    " (snapshot_id, hero_id, item_id, era_id, badge_min, badge_max,"
                    "  wins, matches, avg_purchase_s, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    item_rows,
                )
            else:
                log.warning("baselines: item-stats HTTP %s for era %s badge %d-%d",
                            item_status, era_id, badge_min, badge_max)
    conn.commit()
    log.info("baselines: snapshot %d written", snapshot_id)
    return snapshot_id


def refresh_assets(conn, client, *, now=utcnow) -> None:
    """Reload heroes and items from the assets API (archive raw first)."""
    fetched_at = now().isoformat()
    for path, loader in (("/v1/assets/heroes", load_heroes), ("/v1/assets/items", load_items)):
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
