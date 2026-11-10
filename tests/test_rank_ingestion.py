"""Rank ingestion + the rank-over-time read path (Overview "Rank over time").

Covers:
- discovery derives rank rows from match-history's ranked_display_badge
  (mmr-history was removed from deadlock-api in Nov 2026, spike 15), idempotently,
  skipping calibration and unranked games, and never touching older rows;
- mmr_series returns the full series ordered by the rows' OWN timestamp and drops
  nothing for matches we never ingested (the old INNER JOIN bug);
- the numeric badge maps to the right display rank at tier/subtier boundaries.

All HTTP is mocked (hard rule 3) via FakeClient + the trimmed fixture
match_history_ranked_891231519.json (six real rows saved by spike 15).
"""
import json

from api import queries
from api.service import resolve_badge
from ingest.accounts import add_account
from ingest.discovery import discover_account
from ingest.util import unix_to_iso
from tracker.reference import load_ranks

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture, ok

ME = 891231519
FIXTURE = "match_history_ranked_891231519.json"
WHEN = "2026-06-27T00:00:00+00:00"


def _client(body: str | None = None) -> FakeClient:
    client = FakeClient()
    client.add("match-history", ok(body if body is not None else fixture_text(FIXTURE)))
    return client


def _discover(conn, client) -> None:
    add_account(conn, ME, display_name="me", is_self=True, now=ManualNow())
    discover_account(conn, client, ME, now=ManualNow())


def _rows(conn, account_id=ME):
    return conn.execute(
        "SELECT match_id, badge, recorded_at FROM account_rank_history"
        " WHERE account_id = ? ORDER BY match_id",
        (account_id,),
    ).fetchall()


# ── Ingestion: rank rows come from match-history during discovery ─────────────

def test_discovery_writes_a_rank_row_per_ranked_game(db):
    _discover(db, _client())
    # The fixture holds a calibration game (badge 0), the placement game (34),
    # three more Ranked games (34, 36, and 45 in Beta Season 2) and one Standard
    # game (badge null). Only the four with a real badge become rank rows.
    by_start = {r["match_id"]: r["start_time"] for r in load_fixture(FIXTURE)}
    assert [tuple(r) for r in _rows(db)] == [
        (97559026, 34, unix_to_iso(by_start[97559026])),
        (98476826, 34, unix_to_iso(by_start[98476826])),
        (131359578, 36, unix_to_iso(by_start[131359578])),
        (131908805, 45, unix_to_iso(by_start[131908805])),
    ]


def test_rank_rows_cost_no_extra_request(db):
    client = _client()
    _discover(db, client)
    assert len(client.calls) == 1  # just the match-history call discovery already made
    assert client.calls_matching("mmr-history") == []


def test_rediscovery_is_idempotent(db):
    _discover(db, _client())
    first = [tuple(r) for r in _rows(db)]
    discover_account(db, _client(), ME, now=ManualNow())
    assert [tuple(r) for r in _rows(db)] == first


def test_older_rank_rows_are_left_alone(db):
    # Legacy rows came from the old mmr-history endpoint and can't be re-derived
    # (their match-history rows carry no badge). Discovery must never overwrite or
    # delete them -- here one even shares a match_id with the fixture's Standard game.
    standard = next(r for r in load_fixture(FIXTURE) if r["match_mode"] == 1)
    db.execute(
        "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
        " VALUES (?, ?, 55, '2026-06-01T00:00:00+00:00'), (?, 1, 66, '2025-01-01T00:00:00+00:00')",
        (ME, standard["match_id"], ME),
    )
    db.commit()
    _discover(db, _client())
    badges = {r["match_id"]: r["badge"] for r in _rows(db)}
    assert badges[standard["match_id"]] == 55
    assert badges[1] == 66


def test_malformed_ranked_rows_are_skipped(db):
    good = {"match_id": 7, "start_time": 1_793_914_811, "match_mode": 4,
            "ranked_display_badge": 45}
    body = json.dumps([
        good,
        {**good, "match_id": 8, "ranked_display_badge": "45"},   # not an int
        {**good, "match_id": 9, "start_time": None},             # can't be dated
        {**good, "match_id": None},                              # can't be keyed
    ])
    _discover(db, _client(body))
    assert [r["match_id"] for r in _rows(db)] == [7]


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
    load_ranks(db, load_fixture("assets_ranks.json"), WHEN)  # seasonal tiers 0,1,6,11
    tiers = queries.list_ranks(db)

    obscurus = resolve_badge(0, "seasonal", tiers)
    assert (obscurus["tier"], obscurus["subtier"], obscurus["name"]) == (0, 0, "Obscurus")

    tier1_sub0 = resolve_badge(10, "seasonal", tiers)
    assert (tier1_sub0["tier"], tier1_sub0["subtier"]) == (1, 0)

    tier1_sub6 = resolve_badge(16, "seasonal", tiers)
    assert (tier1_sub6["tier"], tier1_sub6["subtier"]) == (1, 6)

    tier6 = resolve_badge(63, "seasonal", tiers)
    assert (tier6["tier"], tier6["subtier"]) == (6, 3)

    eternus = resolve_badge(116, "seasonal", tiers)
    assert (eternus["tier"], eternus["subtier"], eternus["name"]) == (11, 6, "Eternus")
    # Art URL is derived from the tier, in the seasonal system's layout.
    assert eternus["badge_url"].endswith("/rank11_lg.png")


def test_resolve_badge_none_is_none(db):
    assert resolve_badge(None, "seasonal", queries.list_ranks(db)) is None


def test_resolve_badge_unknown_tier_keeps_numbers(db):
    # A tier with no row in `ranks` still resolves the numbers, just no name/color.
    out = resolve_badge(95, "seasonal", [])  # tier 9 absent
    assert (out["tier"], out["subtier"], out["name"], out["color"]) == (9, 5, None, None)
