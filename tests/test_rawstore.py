"""tracker/rawstore.py: compress raw_json on write, transparently read both
compressed (new) and uncompressed (legacy) rows.

The core guarantee is lossless round-trip: whatever future feature backfills
from matches.raw_json (soul curves, death timing, a re-parse) must get back
the exact bytes that were fetched, so `load(dump(s)) == s` is the headline
test. Legacy passthrough matters just as much -- the compress-legacy
maintenance task runs in batches, so a database always holds a mix of TEXT
and BLOB rows and every read must handle both.
"""
import json
import zlib

from tracker import rawstore


def test_round_trips_byte_identical():
    body = json.dumps({"match_info": {"players": [{"i": i} for i in range(500)]}})
    stored = rawstore.dump(body)
    assert isinstance(stored, bytes)
    assert rawstore.load(stored) == body


def test_compresses_large_bodies():
    # A metadata-shaped body compresses substantially; the whole point is disk.
    body = json.dumps({"x": "abc" * 100_000})
    assert len(rawstore.dump(body)) < len(body.encode("utf-8"))


def test_legacy_str_passes_through_unchanged():
    # sqlite3 hands back TEXT cells as str; those are pre-compression rows.
    assert rawstore.load('{"legacy": true}') == '{"legacy": true}'


def test_none_stays_none():
    assert rawstore.load(None) is None


def test_non_zlib_bytes_fall_back_to_utf8():
    # Defensive: a bytes value that isn't zlib output is decoded, never raised.
    assert rawstore.load(b'{"plain": 1}') == '{"plain": 1}'


def test_load_of_raw_zlib_bytes():
    # A bare zlib stream (no dump wrapper) still decompresses.
    raw = zlib.compress(b'{"a": 1}', 6)
    assert rawstore.load(raw) == '{"a": 1}'
