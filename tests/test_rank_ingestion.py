"""Rank ingestion + the rank-over-time read path (Overview "Rank over time").

Covers the three guarantees from the plan:
- ingestion upserts rank rows idempotently from an archived mmr-history sample;
- mmr_series returns the full series ordered by the rows' OWN timestamp and drops
  nothing for matches we never ingested (the old INNER JOIN bug);
- the numeric badge maps to the right display rank at tier/subtier boundaries.

All HTTP is mocked (hard rule 3) via FakeClient + the trimmed fixture
mmr_history_891231519.json (saved from spike 12).
"""
from api import queries
from api.service import resolve_badge
from ingest.ranks import fetch_account_rank, run_rank_sync
from ingest.runner import _accounts_with_new_matches
from tracker.reference import load_ranks

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture, ok

ME = 891231519
FIXTURE = "mmr_history_891231519.json"
WHEN = "2026-06-27T00:00:00+00:00"


def _client() -> FakeClient:
    client = FakeClient()
    client.add(f"/v1/players/{ME}/mmr-history", ok(fixture_text(FIXTURE)))
    return client


def _rows(conn, account_id=ME):
    return conn.execute(
        "SELECT match_id, badge, recorded_at FROM account_rank_history"
        " WHERE account_id = ? ORDER BY match_id",
        (account_id,),
    ).fetchall()


# ── Ingestion: idempotent upsert from an archived sample ──────────────────────

def test_fetch_account_rank_upserts_all_rows(db):
    sample = load_fixture(FIXTURE)
    n = fetch_account_rank(db, _client(), ME, now=ManualNow())
    assert n == len(sample)

    rows = _rows(db)
    assert len(rows) == len(sample)
    # badge == the endpoint's `rank`; recorded_at is the row's own start_time (ISO).
    by_match = {r["match_id"]: r for r in rows}
    for src in sample:
        got = by_match[src["match_id"]]
        assert got["badge"] == src["rank"]
        assert got["recorded_at"] is not None
        assert got["recorded_at"].startswith("20")  # ISO timestamp, not raw unix


def test_fetch_account_rank_is_idempotent(db):
    sample = load_fixture(FIXTURE)
    first = fetch_account_rank(db, _client(), ME, now=ManualNow())
    second = fetch_account_rank(db, _client(), ME, now=ManualNow())

    assert first == second == len(sample)
    # Re-running must not duplicate: still exactly one row per match_id.
    rows = _rows(db)
    assert len(rows) == len(sample)
    assert len({r["match_id"] for r in rows}) == len(sample)


def test_fetch_account_rank_archives_raw(db):
    fetch_account_rank(db, _client(), ME, now=ManualNow())
    archived = db.execute(
        "SELECT body FROM raw_api_responses WHERE url LIKE '%mmr-history%'"
    ).fetchall()
    assert len(archived) == 1
    assert load_fixture(FIXTURE) == __import__("json").loads(archived[0]["body"])


def test_fetch_account_rank_non_200_writes_nothing(db):
    client = FakeClient()
    client.add("mmr-history", (404, {}, "not found"))
    assert fetch_account_rank(db, client, ME, now=ManualNow()) == 0
    assert _rows(db) == []
    # ...but the raw response was still archived (archive-before-parse).
    assert db.execute(
        "SELECT COUNT(*) FROM raw_api_responses WHERE url LIKE '%mmr-history%'"
    ).fetchone()[0] == 1


def test_run_rank_sync_fetches_each_account(db):
    other = 238668046
    client = FakeClient()
    client.add(f"/v1/players/{ME}/mmr-history", ok(fixture_text(FIXTURE)))
    client.add(f"/v1/players/{other}/mmr-history", ok("[]"))

    total = run_rank_sync(db, client, [ME, other], now=ManualNow())
    assert total == len(load_fixture(FIXTURE))  # other returned no rows
    assert client.calls_matching("mmr-history")  # both accounts were hit
    assert len(client.calls_matching("mmr-history")) == 2


# ── Cadence gate: only accounts with new matches get a rank fetch ─────────────

def test_accounts_with_new_matches_gate():
    assert _accounts_with_new_matches({ME: 3, 222: 0, 333: 1}) == [ME, 333]
    assert _accounts_with_new_matches({ME: 0, 222: 0}) == []


# ── Read path: mmr_series is complete and time-ordered ────────────────────────

def _insert_rank(conn, match_id, badge, recorded_at, account_id=ME):
    conn.execute(
        "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
        " VALUES (?, ?, ?, ?)",
        (account_id, match_id, badge, recorded_at),
    )


def test_mmr_series_keeps_non_ingested_matches(db):
    """The old INNER JOIN to matches dropped rank points for matches we never
    ingested. None of these match_ids exist in `matches`, yet all must appear."""
    _insert_rank(db, 1001, 30, "2026-01-01T00:00:00+00:00")
    _insert_rank(db, 1002, 40, "2026-02-01T00:00:00+00:00")
    _insert_rank(db, 1003, 55, "2026-03-01T00:00:00+00:00")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0

    series = queries.mmr_series(db, ME)
    assert [p["match_id"] for p in series] == [1001, 1002, 1003]
    assert [p["badge"] for p in series] == [30, 40, 55]


def test_mmr_series_orders_by_recorded_at(db):
    # Insert out of chronological order; the series must come back ascending by
    # recorded_at regardless of insertion or match_id order.
    _insert_rank(db, 5, 55, "2026-03-01T00:00:00+00:00")
    _insert_rank(db, 1, 10, "2026-01-01T00:00:00+00:00")
    _insert_rank(db, 9, 40, "2026-02-01T00:00:00+00:00")
    db.commit()

    series = queries.mmr_series(db, ME)
    assert [p["start_time"] for p in series] == [
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
    ]


def test_mmr_series_is_per_account(db):
    _insert_rank(db, 1, 10, "2026-01-01T00:00:00+00:00", account_id=ME)
    _insert_rank(db, 2, 20, "2026-01-02T00:00:00+00:00", account_id=999)
    db.commit()
    assert [p["match_id"] for p in queries.mmr_series(db, ME)] == [1]


# ── Badge -> display rank mapping (boundaries) ────────────────────────────────

def test_resolve_badge_boundaries(db):
    load_ranks(db, load_fixture("assets_ranks.json"), WHEN)  # tiers 0,1,6,11
    tiers = queries.list_ranks(db)

    obscurus = resolve_badge(0, tiers)
    assert (obscurus["tier"], obscurus["subtier"], obscurus["name"]) == (0, 0, "Obscurus")

    tier1_sub0 = resolve_badge(10, tiers)
    assert (tier1_sub0["tier"], tier1_sub0["subtier"]) == (1, 0)

    tier1_sub6 = resolve_badge(16, tiers)
    assert (tier1_sub6["tier"], tier1_sub6["subtier"]) == (1, 6)

    tier6 = resolve_badge(63, tiers)
    assert (tier6["tier"], tier6["subtier"]) == (6, 3)

    eternus = resolve_badge(116, tiers)
    assert (eternus["tier"], eternus["subtier"], eternus["name"]) == (11, 6, "Eternus")
    # Art URL is derived from the tier.
    assert eternus["badge_url"].endswith("/rank11/badge_lg.png")


def test_resolve_badge_none_is_none(db):
    assert resolve_badge(None, queries.list_ranks(db)) is None


def test_resolve_badge_unknown_tier_keeps_numbers(db):
    # A tier with no row in `ranks` still resolves the numbers, just no name/color.
    out = resolve_badge(95, [])  # tier 9 absent
    assert (out["tier"], out["subtier"], out["name"], out["color"]) == (9, 5, None, None)
