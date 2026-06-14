"""Unit tests for ingest.maintenance: revive, baselines, assets, summary."""
from datetime import datetime, timezone

from ingest.maintenance import (
    DECADE_BRACKETS,
    refresh_baselines,
    revive_unavailable,
    run_maintenance,
)

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture, ok

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def seed_eras(db) -> tuple[int, int]:
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('jan', '2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('jun', '2026-06-01T00:00:00+00:00')")
    db.commit()
    ids = [r["era_id"] for r in db.execute("SELECT era_id FROM patch_eras ORDER BY started_at")]
    return ids[0], ids[1]


def queue_unavailable(db, match_id: int, last_attempt_iso: str) -> None:
    db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status, attempts, last_attempt_at)"
        " VALUES (?, ?, 'unavailable', 5, ?)",
        (match_id, last_attempt_iso, last_attempt_iso),
    )
    db.commit()


# ── Re-queue of stale unavailable matches ────────────────────────────────────

def test_revive_only_touches_rows_older_than_24h(db):
    now = ManualNow(NOW)
    queue_unavailable(db, 1, "2026-06-10T11:00:00+00:00")  # 25h old -> revived
    queue_unavailable(db, 2, "2026-06-11T06:00:00+00:00")  # 6h old -> left alone

    assert revive_unavailable(db, now=now) == 1

    rows = {r["match_id"]: r for r in db.execute("SELECT * FROM fetch_queue")}
    assert rows[1]["status"] == "pending" and rows[1]["attempts"] == 0
    assert rows[2]["status"] == "unavailable" and rows[2]["attempts"] == 5


# ── Baseline refresh ─────────────────────────────────────────────────────────

def test_refresh_baselines_one_call_per_bracket_per_era_plus_all_time(db):
    era_jan, era_jun = seed_eras(db)
    now = ManualNow(NOW)
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))

    refresh_baselines(db, client, now=now)

    n_brackets = len(DECADE_BRACKETS)  # 12 gapless decade brackets
    n_spans = 3  # two eras + one explicit all-time span
    calls = client.calls_matching("hero-counter-stats")
    # Matchups are fetched twice per bracket: overall + same-lane.
    assert len(calls) == 2 * n_spans * n_brackets
    same_lane_calls = [u for u in calls if "same_lane_filter=true" in u]
    assert len(same_lane_calls) == n_spans * n_brackets          # exactly half
    assert len(calls) - len(same_lane_calls) == n_spans * n_brackets
    # NEVER omit the date params: the endpoint defaults to a trailing 30-day
    # window (api-findings contradiction 7). Every call also carries the decade
    # badge filter and the STRING game_mode variant (contradiction 8).
    for url in calls:
        assert "min_unix_timestamp=" in url
        assert "max_unix_timestamp=" in url
        assert "min_average_badge=" in url and "max_average_badge=" in url
        assert "game_mode=normal" in url
    now_unix = int(NOW.timestamp())
    assert any(
        f"min_unix_timestamp={unix('2026-01-01T00:00:00+00:00')}" in u
        and f"max_unix_timestamp={unix('2026-06-01T00:00:00+00:00')}" in u
        for u in calls
    ), "jan era bounded by jun era's start"
    assert any(
        f"min_unix_timestamp={unix('2026-06-01T00:00:00+00:00')}" in u
        and f"max_unix_timestamp={now_unix}" in u
        for u in calls
    ), "open era bounded by now"
    assert any(f"min_unix_timestamp=0" in u and f"max_unix_timestamp={now_unix}" in u
               for u in calls), "all-time span is explicit"

    n_fixture_rows = len(load_fixture("counter_stats.json"))
    rows = db.execute("SELECT * FROM baseline_hero_matchups").fetchall()
    # Overall + same-lane rows for every (span, bracket, fixture row).
    assert len(rows) == 2 * n_spans * n_brackets * n_fixture_rows
    assert {r["same_lane"] for r in rows} == {0, 1}
    assert {r["era_id"] for r in rows} == {era_jan, era_jun, 0}  # 0 = all-time sentinel
    # One row group per decade bracket; no all-ranks (0,116) row is written
    # (full-range baseline is rated-only, re-summed from the brackets).
    assert {(r["badge_min"], r["badge_max"]) for r in rows} == set(DECADE_BRACKETS)
    snapshots = db.execute("SELECT * FROM baseline_snapshots").fetchall()
    assert len(snapshots) == 1
    assert all(r["snapshot_id"] == snapshots[0]["snapshot_id"] for r in rows)
    # Responses archived before parsing (hard rule 2) -- both variants.
    archived = db.execute(
        "SELECT COUNT(*) FROM raw_api_responses WHERE url LIKE '%hero-counter-stats%'"
    ).fetchone()[0]
    assert archived == 2 * n_spans * n_brackets


def test_refresh_baselines_fills_item_stats_via_bucket_hero(db):
    seed_eras(db)
    now = ManualNow(NOW)
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))

    refresh_baselines(db, client, now=now)

    n_brackets = len(DECADE_BRACKETS)
    n_spans = 3  # 2 eras + all-time
    # bucket=hero is one call per (era span, decade bracket).
    item_calls = client.calls_matching("item-stats")
    assert len(item_calls) == n_spans * n_brackets
    for url in item_calls:
        assert "bucket=hero" in url
        assert "min_unix_timestamp=" in url and "max_unix_timestamp=" in url
        assert "min_average_badge=" in url and "max_average_badge=" in url
        assert "game_mode=normal" in url

    rows = db.execute("SELECT * FROM baseline_hero_item_stats").fetchall()
    n_fixture = len(load_fixture("item_stats_bucket_hero.json"))
    assert len(rows) == n_spans * n_brackets * n_fixture
    # The bucket field maps to hero_id; avg_buy_time_s -> avg_purchase_s.
    fixture_heroes = {r["bucket"] for r in load_fixture("item_stats_bucket_hero.json")}
    assert {r["hero_id"] for r in rows} == fixture_heroes
    assert all(r["avg_purchase_s"] is not None for r in rows)
    assert {(r["badge_min"], r["badge_max"]) for r in rows} == set(DECADE_BRACKETS)


def test_old_snapshots_are_kept_for_time_travel(db):
    seed_eras(db)
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    refresh_baselines(db, client, now=ManualNow(NOW))
    refresh_baselines(db, client, now=ManualNow(NOW))
    assert db.execute("SELECT COUNT(*) FROM baseline_snapshots").fetchone()[0] == 2
    distinct = db.execute(
        "SELECT COUNT(DISTINCT snapshot_id) FROM baseline_hero_matchups"
    ).fetchone()[0]
    assert distinct == 2


# ── The full nightly job ─────────────────────────────────────────────────────

def full_client() -> FakeClient:
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))
    client.add("GetNewsForApp", ok(fixture_text("steam_news.json")))
    return client


def test_run_maintenance_does_all_jobs_and_stamps_meta(db):
    seed_eras(db)
    queue_unavailable(db, 42, "2026-06-09T00:00:00+00:00")
    now = ManualNow(NOW)

    summary = run_maintenance(db, full_client(), now=now)

    assert summary["revived"] == 1
    assert db.execute("SELECT COUNT(*) FROM heroes").fetchone()[0] > 0
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] > 0
    assert db.execute("SELECT COUNT(*) FROM ranks").fetchone()[0] > 0
    assert db.execute("SELECT COUNT(*) FROM era_candidates").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM baseline_snapshots").fetchone()[0] == 1
    stamp = db.execute(
        "SELECT value FROM worker_meta WHERE key='last_maintenance_at'"
    ).fetchone()
    assert stamp is not None and stamp["value"] == NOW.isoformat()
    # Summary counts by queue status for the one-line log.
    assert "queue" in summary
