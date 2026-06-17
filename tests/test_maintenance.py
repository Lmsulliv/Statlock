"""Unit tests for ingest.maintenance: revive, baselines, assets, summary."""
import logging
from datetime import datetime, timezone

from ingest.maintenance import (
    ALL_TIME_ERA_ID,
    DAY_S,
    DECADE_BRACKETS,
    MONTHLY_S,
    WEEKLY_S,
    EraSpan,
    _refresh_due,
    refresh_baselines,
    revive_unavailable,
    run_maintenance,
)

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture, ok

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
N_BRACKETS = len(DECADE_BRACKETS)   # 12 gapless decade brackets


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


# ── Baseline refresh: decade brackets ────────────────────────────────────────

def _baseline_client() -> FakeClient:
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    return client


def test_refresh_baselines_writes_decade_brackets_each_era(db):
    era_jan, era_jun = seed_eras(db)
    now = ManualNow(NOW)
    client = _baseline_client()

    snapshot_id = refresh_baselines(db, client, now=now)

    n_spans = 3  # two eras + the explicit all-time span; first run, all due
    calls = client.calls_matching("hero-counter-stats")
    # One counter call per (decade bracket, same_lane in {0, 1}) per span.
    assert len(calls) == 2 * N_BRACKETS * n_spans
    same_lane_calls = [u for u in calls if "same_lane_filter=true" in u]
    assert len(same_lane_calls) == N_BRACKETS * n_spans          # exactly half
    # NEVER omit the date params (api-findings contradiction 7). Every call also
    # carries the decade badge filter and the STRING game_mode (cont. 8).
    for url in calls:
        assert "min_unix_timestamp=" in url and "max_unix_timestamp=" in url
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

    rows = db.execute("SELECT * FROM baseline_hero_matchups").fetchall()
    # One row group per decade bracket; no all-ranks (0,116) row (full-range
    # baseline is rated-only, re-summed from the brackets).
    assert {(r["badge_min"], r["badge_max"]) for r in rows} == set(DECADE_BRACKETS)
    assert {r["same_lane"] for r in rows} == {0, 1}
    assert {r["era_id"] for r in rows} == {era_jan, era_jun, 0}  # 0 = all-time sentinel
    n_fixture = len(load_fixture("counter_stats.json"))
    assert len(rows) == 2 * N_BRACKETS * n_spans * n_fixture
    # Single evolving snapshot.
    snapshots = db.execute("SELECT * FROM baseline_snapshots").fetchall()
    assert len(snapshots) == 1
    assert all(r["snapshot_id"] == snapshot_id for r in rows)
    # Responses archived before parsing (hard rule 2) -- both same_lane variants.
    archived = db.execute(
        "SELECT COUNT(*) FROM raw_api_responses WHERE url LIKE '%hero-counter-stats%'"
    ).fetchone()[0]
    assert archived == 2 * N_BRACKETS * n_spans


def test_refresh_baselines_item_via_bucket_hero(db):
    seed_eras(db)
    now = ManualNow(NOW)
    client = _baseline_client()

    refresh_baselines(db, client, now=now)

    n_spans = 3  # 2 eras + all-time
    item_calls = client.calls_matching("item-stats")
    # bucket=hero is one call per (decade bracket, span).
    assert len(item_calls) == N_BRACKETS * n_spans
    for url in item_calls:
        assert "bucket=hero" in url
        assert "min_unix_timestamp=" in url and "max_unix_timestamp=" in url
        assert "min_average_badge=" in url and "max_average_badge=" in url
        assert "game_mode=normal" in url

    rows = db.execute("SELECT * FROM baseline_hero_item_stats").fetchall()
    assert {(r["badge_min"], r["badge_max"]) for r in rows} == set(DECADE_BRACKETS)
    n_fixture = len(load_fixture("item_stats_bucket_hero.json"))
    assert len(rows) == N_BRACKETS * n_spans * n_fixture
    # The bucket field maps to hero_id; avg_buy_time_s -> avg_purchase_s.
    fixture_heroes = {r["bucket"] for r in load_fixture("item_stats_bucket_hero.json")}
    assert {r["hero_id"] for r in rows} == fixture_heroes
    assert all(r["avg_purchase_s"] is not None for r in rows)


# ── A 200 is not a promise of a JSON body ────────────────────────────────────
#
# The analytics endpoints occasionally answer 200 with an empty or truncated
# body; json.loads("") raises JSONDecodeError. A single bad bracket must warn
# and skip, never abort the whole nightly refresh. The counter route is given
# two responses: the FIRST call gets the bad body, every later call repeats the
# good fixture (FakeClient repeats its last response). So exactly one counter
# call's worth of rows is missing and every following bracket/era still lands.

N_SPANS = 3   # two seeded eras + the explicit all-time span; first run, all due


def test_empty_200_counter_body_warns_and_skips_one_call(db, caplog):
    seed_eras(db)
    now = ManualNow(NOW)
    client = FakeClient()
    client.add("hero-counter-stats", (200, {}, ""), ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))

    with caplog.at_level(logging.WARNING):
        refresh_baselines(db, client, now=now)   # must not raise

    n_fixture = len(load_fixture("counter_stats.json"))
    total = db.execute("SELECT COUNT(*) FROM baseline_hero_matchups").fetchone()[0]
    # Every counter call but the one empty body inserted its fixture rows.
    assert total == (2 * N_BRACKETS * N_SPANS - 1) * n_fixture
    # A later, wholly-good bracket still landed (remaining work proceeds).
    assert db.execute(
        "SELECT COUNT(*) FROM baseline_hero_matchups WHERE badge_min = 10"
    ).fetchone()[0] > 0
    assert "empty 200 body" in caplog.text and "hero-counter-stats" in caplog.text


def test_empty_200_item_body_warns_and_skips_one_call(db, caplog):
    seed_eras(db)
    now = ManualNow(NOW)
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", (200, {}, ""), ok(fixture_text("item_stats_bucket_hero.json")))

    with caplog.at_level(logging.WARNING):
        refresh_baselines(db, client, now=now)   # must not raise

    n_item = len(load_fixture("item_stats_bucket_hero.json"))
    item_total = db.execute("SELECT COUNT(*) FROM baseline_hero_item_stats").fetchone()[0]
    assert item_total == (N_BRACKETS * N_SPANS - 1) * n_item
    # Counter baselines are untouched by an item-stats hiccup.
    n_counter = len(load_fixture("counter_stats.json"))
    assert db.execute("SELECT COUNT(*) FROM baseline_hero_matchups").fetchone()[0] == \
        2 * N_BRACKETS * N_SPANS * n_counter
    assert "empty 200 body" in caplog.text and "item-stats" in caplog.text


def test_malformed_200_body_warns_and_skips_one_call(db, caplog):
    seed_eras(db)
    now = ManualNow(NOW)
    client = FakeClient()
    client.add("hero-counter-stats", (200, {}, "not json"), ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))

    with caplog.at_level(logging.WARNING):
        refresh_baselines(db, client, now=now)   # must not raise

    n_fixture = len(load_fixture("counter_stats.json"))
    total = db.execute("SELECT COUNT(*) FROM baseline_hero_matchups").fetchone()[0]
    assert total == (2 * N_BRACKETS * N_SPANS - 1) * n_fixture
    assert "malformed 200 body" in caplog.text and "hero-counter-stats" in caplog.text


def test_valid_200_body_still_inserts_rows(db):
    """Regression: the parse helper must not change the happy path."""
    seed_eras(db)
    now = ManualNow(NOW)

    refresh_baselines(db, _baseline_client(), now=now)

    n_fixture = len(load_fixture("counter_stats.json"))
    assert db.execute("SELECT COUNT(*) FROM baseline_hero_matchups").fetchone()[0] == \
        2 * N_BRACKETS * N_SPANS * n_fixture


# ── Staggered refresh + single evolving snapshot ─────────────────────────────

def test_second_run_refreshes_open_era_only_and_keeps_snapshot_complete(db):
    era_jan, era_jun = seed_eras(db)
    now = ManualNow(NOW)
    client = _baseline_client()

    snapshot_id = refresh_baselines(db, client, now=now)   # run 1: full rebuild
    before_second = len(client.calls)

    now.advance(DAY_S)                                      # +1 day
    refresh_baselines(db, client, now=now)                 # run 2: staggered

    # A day later only the open era (jun) is due: jan closed <30d ago -> weekly
    # (not due after 1 day); the all-time sentinel is monthly. So run 2 makes one
    # span's worth of calls: (2 counter + 1 item) * 12 brackets.
    new_calls = client.calls[before_second:]
    assert len(new_calls) == 3 * N_BRACKETS
    open_max = int(now.t.timestamp())
    jun_start = unix("2026-06-01T00:00:00+00:00")
    assert all(f"min_unix_timestamp={jun_start}" in u and f"max_unix_timestamp={open_max}" in u
               for u in new_calls), "run 2 only touches the open-era window"

    # No new snapshot: the single snapshot evolved in place.
    assert db.execute("SELECT COUNT(*) FROM baseline_snapshots").fetchone()[0] == 1

    # Refresh state advanced for the open era only.
    state = {r["era_id"]: r["last_refreshed_at"]
             for r in db.execute("SELECT * FROM baseline_refresh_state")}
    assert state[era_jun] == now.t.isoformat()             # re-fetched
    assert state[era_jan] == NOW.isoformat()               # skipped, unchanged
    assert state[ALL_TIME_ERA_ID] == NOW.isoformat()       # skipped, unchanged

    # The evolving snapshot still holds the skipped era's rows, so reads (which
    # use MAX(snapshot_id)) stay complete.
    jan_rows = db.execute(
        "SELECT COUNT(*) FROM baseline_hero_matchups WHERE snapshot_id = ? AND era_id = ?",
        (snapshot_id, era_jan),
    ).fetchone()[0]
    assert jan_rows > 0


def test_refresh_due_cadence():
    now_unix = unix("2026-06-15T00:00:00+00:00")

    def ago(seconds: int) -> str:
        return datetime.fromtimestamp(now_unix - seconds, tz=timezone.utc).isoformat()

    open_era = EraSpan(2, 0, now_unix, True, None)
    assert _refresh_due(open_era, now_unix, None)           # never fetched -> due
    assert _refresh_due(open_era, now_unix, ago(0))         # open era -> always due

    all_time = EraSpan(ALL_TIME_ERA_ID, 0, now_unix, False, None)
    assert not _refresh_due(all_time, now_unix, ago(WEEKLY_S))      # 7d < monthly
    assert _refresh_due(all_time, now_unix, ago(MONTHLY_S + 1))     # monthly

    recent = EraSpan(1, 0, now_unix - 10 * DAY_S, False, now_unix - 10 * DAY_S)
    assert not _refresh_due(recent, now_unix, ago(3 * DAY_S))       # weekly: 3d < 7d
    assert _refresh_due(recent, now_unix, ago(WEEKLY_S + 1))

    old = EraSpan(1, 0, now_unix - 60 * DAY_S, False, now_unix - 60 * DAY_S)
    assert not _refresh_due(old, now_unix, ago(WEEKLY_S))           # monthly: 7d < 30d
    assert _refresh_due(old, now_unix, ago(MONTHLY_S + 1))


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
    # Three of the four fixture posts are flagged (all Valve Community
    # Announcements; the PC Gamer article is skipped). See test_era_candidates.
    assert db.execute("SELECT COUNT(*) FROM era_candidates").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM baseline_snapshots").fetchone()[0] == 1
    stamp = db.execute(
        "SELECT value FROM worker_meta WHERE key='last_maintenance_at'"
    ).fetchone()
    assert stamp is not None and stamp["value"] == NOW.isoformat()
    # Summary counts by queue status for the one-line log.
    assert "queue" in summary
