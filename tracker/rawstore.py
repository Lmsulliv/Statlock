"""Compressed storage for large archived payloads (matches.raw_json).

Match metadata bodies run 1.2-1.6 MB each. Stored verbatim they dominate the
database file, so we zlib-compress them on the way in and transparently
decompress on the way out. The format is deliberately migration-free: SQLite
is dynamically typed, so a compressed body is written as a BLOB into the same
`raw_json TEXT` column that legacy rows hold as plain TEXT.

Detection is by Python type, which sqlite3 reports faithfully: a TEXT cell
comes back as `str` (a legacy, uncompressed row) and a BLOB cell as `bytes`
(a compressed row). A belt-and-braces magic-byte check handles the unlikely
case of a `bytes` value that isn't actually zlib output.

Every read of matches.raw_json goes through `load`, and every write through
`dump`, so the rest of the codebase never sees the compressed representation.
"""
import zlib

# zlib streams begin with a 2-byte header; the low byte is 0x78 for the
# default (deflate, 32K window) compression this module produces. We only use
# it as a fallback discriminator -- the Python type is the primary signal.
_ZLIB_MAGIC = 0x78

# Matches the stdlib default; named so the level is stated once.
_LEVEL = 6


def dump(text: str) -> bytes:
    """Compress a raw_json string for storage. Returns bytes (stored as a BLOB).

    Applied to the exact string handed in, so `load(dump(s)) == s` byte for
    byte -- the archive round-trips losslessly."""
    return zlib.compress(text.encode("utf-8"), _LEVEL)


def load(value: str | bytes | None) -> str | None:
    """Return the raw_json text for a stored value, whatever its era.

    - None (no archive) -> None.
    - str -> a legacy uncompressed row; returned unchanged.
    - bytes -> a compressed row; decompressed and decoded. A bytes value that
      doesn't start with the zlib magic byte is treated as raw UTF-8 (defensive:
      such a row shouldn't exist, but we never want a read to raise)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value[:1] and value[0] == _ZLIB_MAGIC:
        return zlib.decompress(value).decode("utf-8")
    return value.decode("utf-8")
