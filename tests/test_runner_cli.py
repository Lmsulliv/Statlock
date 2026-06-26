"""Tests for ingest.runner (run-once flow) and the ingest CLI (status, add-account)."""
import json

from ingest.accounts import add_account
from ingest.runner import maintenance_due, run_once
from ingest.__main__ import main as cli_main

from tests.fakes import FakeClient, FakeSleep, ManualNow, fixture_text, load_fixture, ok

ME = 891231519


def full_client() -> FakeClient:
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(fixture_text(f"match_history_{ME}.json")))
    meta = load_fixture("match_metadata_86714494.json")
    for match_id in (86704689, 86707774, 86714494):
        meta["match_info"]["match_id"] = match_id
        client.add(f"/v1/matches/{match_id}/metadata", ok(json.dumps(meta)))
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))
    client.add("GetNewsForApp", ok(fixture_text("steam_news.json")))
    return client


def test_run_once_does_maintenance_discovery_drain(db):
    now = ManualNow()
    # Migration 013 pre-seeds 12 curated eras; clear them so ingested matches bind
    # to this single epoch era.
    db.execute("DELETE FROM patch_eras")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('all', '1970-01-01T00:00:00+00:00')")
    db.commit()
    add_account(db, ME, display_name="me", is_self=True, now=now)
    client = full_client()

    run_once(db, client, now=now, sleep=FakeSleep(now))

    # Maintenance ran (no stamp existed -> due), discovery queued 3 matches,
    # drain fetched them all.
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3
    statuses = [r["status"] for r in db.execute("SELECT status FROM fetch_queue")]
    assert statuses == ["fetched"] * 3
    assert db.execute(
        "SELECT value FROM worker_meta WHERE key='last_maintenance_at'"
    ).fetchone() is not None

    # Second run within 24h: maintenance skipped (still one news poll).
    run_once(db, client, now=now, sleep=FakeSleep(now))
    assert len(client.calls_matching("GetNewsForApp")) == 1


def test_maintenance_due(db):
    now = ManualNow()
    assert maintenance_due(db, now=now)  # never ran
    db.execute(
        "INSERT INTO worker_meta(key, value) VALUES('last_maintenance_at', ?)",
        (now().isoformat(),),
    )
    db.commit()
    assert not maintenance_due(db, now=now)
    now.advance(25 * 3600)
    assert maintenance_due(db, now=now)


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_add_account_and_status(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")

    cli_main(["--db", db_path, "add-account", "76561198851497247", "--name", "me", "--self"])
    out = capsys.readouterr().out
    assert "891231519" in out

    cli_main(["--db", db_path, "status"])
    out = capsys.readouterr().out
    # Queue depth and counts by status, per the observability section.
    assert "depth" in out.lower()
    assert "pending" in out.lower()
    assert "891231519" in out


def test_cli_status_counts(db, tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "counts.db"
    from tracker.db import connect
    from tracker.migrate import migrate
    conn = connect(db_path)
    migrate(conn)
    conn.execute("INSERT INTO fetch_queue(match_id, discovered_at) VALUES (1, '2026-06-11')")
    conn.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status) VALUES (2, '2026-06-11', 'fetched')"
    )
    conn.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status, attempts) "
        "VALUES (3, '2026-06-11', 'unavailable', 5)"
    )
    conn.commit()
    conn.close()

    cli_main(["--db", str(db_path), "status"])
    out = capsys.readouterr().out.lower()
    assert "pending" in out and "fetched" in out and "unavailable" in out
