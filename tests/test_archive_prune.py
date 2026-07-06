"""One-time archive housekeeping: prune duplicate metadata bodies and compress
legacy raw_json rows.

Both tasks converge a legacy database onto the deduplicated + compressed layout.
The load-bearing properties: prune deletes ONLY a 200 metadata body whose match is
stored with a real raw_json (never an empty-200 or a never-ingested match's body,
which the recovery path still needs), and both tasks are idempotent and batched.
"""
from ingest.client import BASE_URL
from ingest.maintenance import compress_legacy_raw_json, prune_metadata_archive
from tracker import rawstore

JUNE = "2026-06-15T12:00:00+00:00"


def _metadata_url(match_id: int) -> str:
    return f"{BASE_URL}/v1/matches/{match_id}/metadata"


def _store_match(conn, match_id: int, raw_json="{}") -> None:
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, winning_team,"
        " raw_json, ingested_at) VALUES (?, ?, 1800, '0', ?, ?)",
        (match_id, JUNE, raw_json, JUNE),
    )
    conn.commit()


def _archive(conn, url: str, status: int = 200, body: str = '{"x": 1}') -> None:
    conn.execute(
        "INSERT INTO raw_api_responses(url, status_code, body, fetched_at)"
        " VALUES (?, ?, ?, ?)", (url, status, body, JUNE))
    conn.commit()


def _archive_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM raw_api_responses").fetchone()[0]


# ── prune-archive ─────────────────────────────────────────────────────────────

def test_prune_removes_only_duplicated_metadata_bodies(db):
    # (1) stored match with a real body -> its 200 metadata archive is a duplicate.
    _store_match(db, 100, raw_json='{"real": true}')
    _archive(db, _metadata_url(100))
    # (2) 200 metadata for a match NOT stored -> keep (recovery still needs it).
    _archive(db, _metadata_url(200))
    # (3) stored match but empty raw_json -> keep (no real counterpart body).
    _store_match(db, 300, raw_json="")
    _archive(db, _metadata_url(300))
    # (4) non-200 metadata for a stored match -> keep (not a success duplicate).
    _archive(db, _metadata_url(100), status=404, body="not found")
    # (5) a different endpoint entirely -> keep.
    _archive(db, f"{BASE_URL}/v1/analytics/hero-counter-stats?x=1")

    removed = prune_metadata_archive(db)

    assert removed == 1
    remaining = {r["url"] for r in db.execute("SELECT url FROM raw_api_responses")}
    # The 200 duplicate for match 100 is gone; its 404 forensics row survives.
    kinds = db.execute(
        "SELECT status_code FROM raw_api_responses WHERE url = ?",
        (_metadata_url(100),)).fetchall()
    assert [r["status_code"] for r in kinds] == [404]
    assert _metadata_url(200) in remaining
    assert _metadata_url(300) in remaining
    assert any("hero-counter-stats" in u for u in remaining)


def test_prune_is_idempotent(db):
    _store_match(db, 100, raw_json='{"real": true}')
    _archive(db, _metadata_url(100))
    assert prune_metadata_archive(db) == 1
    assert prune_metadata_archive(db) == 0            # nothing left to remove
    assert _archive_count(db) == 0


def test_prune_batches_across_boundary(db):
    # Three deletable rows with batch_size=2 forces a second batch.
    for mid in (100, 101, 102):
        _store_match(db, mid, raw_json='{"real": true}')
        _archive(db, _metadata_url(mid))
    # One survivor (unstored match) proves the loop stops cleanly.
    _archive(db, _metadata_url(999))

    removed = prune_metadata_archive(db, batch_size=2)

    assert removed == 3
    assert _archive_count(db) == 1


# ── compress-raw-json ─────────────────────────────────────────────────────────

def test_compress_converts_legacy_rows_and_round_trips(db):
    body = '{"match_info": {"players": []}}'
    _store_match(db, 100, raw_json=body)                # stored as TEXT (legacy)
    # Legacy TEXT reads correctly BEFORE compression.
    stored = db.execute("SELECT raw_json FROM matches WHERE match_id = 100").fetchone()
    assert isinstance(stored["raw_json"], str)
    assert rawstore.load(stored["raw_json"]) == body

    compressed = compress_legacy_raw_json(db)

    assert compressed == 1
    after = db.execute("SELECT raw_json FROM matches WHERE match_id = 100").fetchone()
    assert isinstance(after["raw_json"], bytes)         # now a compressed BLOB
    assert rawstore.load(after["raw_json"]) == body     # round-trips identically


def test_compress_is_idempotent(db):
    _store_match(db, 100, raw_json='{"a": 1}')
    assert compress_legacy_raw_json(db) == 1
    assert compress_legacy_raw_json(db) == 0            # no TEXT rows remain


def test_compress_batches_across_boundary(db):
    for mid in (100, 101, 102):
        _store_match(db, mid, raw_json='{"a": %d}' % mid)
    assert compress_legacy_raw_json(db, batch_size=2) == 3
    assert compress_legacy_raw_json(db) == 0
