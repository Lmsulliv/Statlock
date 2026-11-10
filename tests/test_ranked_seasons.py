"""Ranked seasons and rank systems (Phase 1 of the season-aware rank chart).

The badge encoding (tier*10 + subtier) is shared by two rank ladders: badge 55 is
Ritualist 5 before the Jul 30 2026 cutover and Mystic 5 after it
(docs/api-findings.md "Ranked seasons and rank systems", spike 15). These tests
pin down:
- season_for at every season start (-1 s / exact / +1 s), before the first
  season, and inside the current one;
- resolve_badge naming a badge by its rank system;
- refresh_assets only ever touching the seasonal system;
- the per-season peak (including a season with no games) and current_rank's season;
- migration 022 on a v21 database whose ranks table holds one system.

All HTTP is mocked (hard rule 3).
"""
from datetime import datetime, timedelta, timezone

import pytest

from api import queries, service
from api.scope import make_scope
from ingest.maintenance import refresh_assets
from tracker.db import connect
from tracker.migrate import _STEPS, migrate
from tracker.reference import load_ranks

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture, ok

ME = 891231519
SEASONAL_FIXTURE = "assets_ranks_seasonal.json"   # trimmed from archive 5782
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)

LEGACY_START = "2024-10-10T00:00:00+00:00"
S1_START = "2026-07-30T17:00:00+00:00"
S2_START = "2026-11-05T20:00:00+00:00"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _shift(iso: str, seconds: int) -> str:
    return _iso(datetime.fromisoformat(iso) + timedelta(seconds=seconds))


def _load_seasonal(conn) -> None:
    load_ranks(conn, load_fixture(SEASONAL_FIXTURE), "2026-11-09T23:23:06+00:00")


def _insert_rank(conn, match_id, badge, recorded_at, account_id=ME):
    conn.execute(
        "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
        " VALUES (?, ?, ?, ?)", (account_id, match_id, badge, recorded_at))


# ── The seed ─────────────────────────────────────────────────────────────────

def test_seasons_are_seeded_with_the_api_dates(db):
    rows = [tuple(r) for r in db.execute(
        "SELECT slug, label, rank_system, started_at FROM ranked_seasons ORDER BY started_at")]
    assert rows == [
        ("legacy", "Legacy ranks", "legacy", LEGACY_START),
        ("beta-season-1", "Beta Season 1", "seasonal", S1_START),
        ("beta-season-2", "Beta Season 2", "seasonal", S2_START),
    ]


# ── season_for: the one timestamp -> season function ─────────────────────────

def _slug(seasons, when):
    season = service.season_for(seasons, when)
    return season["slug"] if season else None


@pytest.mark.parametrize("start, before, at", [
    (LEGACY_START, None, "legacy"),
    (S1_START, "legacy", "beta-season-1"),
    (S2_START, "beta-season-1", "beta-season-2"),
])
def test_season_for_at_each_boundary(db, start, before, at):
    seasons = queries.list_ranked_seasons(db)
    assert _slug(seasons, _shift(start, -1)) == before
    assert _slug(seasons, start) == at          # a season includes its own start
    assert _slug(seasons, _shift(start, 1)) == at


def test_season_for_before_the_first_season_is_none(db):
    assert _slug(queries.list_ranked_seasons(db), "2024-01-01T00:00:00+00:00") is None


def test_season_for_inside_the_current_season(db):
    assert _slug(queries.list_ranked_seasons(db), "2027-01-15T12:00:00+00:00") == "beta-season-2"


def test_season_for_compares_instants_not_strings(db):
    # The same instant written with "Z" sorts BELOW "+00:00" as a string, which
    # would put this exact-boundary timestamp in the previous season.
    assert _slug(queries.list_ranked_seasons(db), "2026-11-05T20:00:00Z") == "beta-season-2"


# ── resolve_badge names a badge by its rank system ───────────────────────────

def test_badge_55_is_ritualist_in_legacy_and_mystic_in_seasonal(db):
    _load_seasonal(db)
    tiers = queries.list_ranks(db)

    legacy = service.resolve_badge(55, "legacy", tiers)
    seasonal = service.resolve_badge(55, "seasonal", tiers)

    assert (legacy["name"], legacy["subtier"], legacy["rank_system"]) == ("Ritualist", 5, "legacy")
    assert (seasonal["name"], seasonal["subtier"], seasonal["rank_system"]) == ("Mystic", 5, "seasonal")
    # Each system has its own badge art (both paths still resolve on the CDN).
    assert legacy["badge_url"].endswith("/ranks/rank5/badge_lg.png")
    assert seasonal["badge_url"].endswith("/ranks/rank05_lg.png")


# ── refresh_assets only writes the seasonal system ───────────────────────────

def test_refresh_assets_with_new_names_leaves_legacy_untouched(db):
    legacy_before = [tuple(r) for r in db.execute(
        "SELECT tier, name, color, fetched_at FROM ranks WHERE rank_system = 'legacy' ORDER BY tier")]
    assert len(legacy_before) == 12

    client = FakeClient()
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text(SEASONAL_FIXTURE)))
    refresh_assets(db, client, now=ManualNow())

    legacy_after = [tuple(r) for r in db.execute(
        "SELECT tier, name, color, fetched_at FROM ranks WHERE rank_system = 'legacy' ORDER BY tier")]
    assert legacy_after == legacy_before
    seasonal = dict(db.execute(
        "SELECT tier, name FROM ranks WHERE rank_system = 'seasonal'").fetchall())
    assert len(seasonal) == 12 and seasonal[5] == "Mystic"


# ── The rank timeline: points, seasons, peak, current_rank ───────────────────

def _seed_history(conn) -> None:
    _load_seasonal(conn)
    _insert_rank(conn, 1, 0, "2024-06-01T00:00:00+00:00")     # before ranks existed
    _insert_rank(conn, 2, 55, "2026-06-01T00:00:00+00:00")    # legacy Ritualist 5
    _insert_rank(conn, 3, 71, "2026-07-20T00:00:00+00:00")    # legacy Archon 1
    _insert_rank(conn, 4, 34, "2026-08-05T03:16:17+00:00")    # S1 placement
    _insert_rank(conn, 5, 55, "2026-09-01T00:00:00+00:00")    # S1 Mystic 5 (peak)
    _insert_rank(conn, 6, 36, "2026-10-31T20:26:16+00:00")    # S1, latest; no S2 games
    conn.commit()


def test_series_points_are_resolved_by_their_own_season(db):
    _seed_history(db)
    series = service.rank_timeline(db, ME, now=lambda: NOW)["mmr_series"]

    # The pre-ranks point has no season (no rank system to name it) -> left out.
    assert [p["match_id"] for p in series] == [2, 3, 4, 5, 6]
    by_id = {p["match_id"]: p for p in series}
    assert (by_id[2]["season"], by_id[2]["rank_system"], by_id[2]["name"]) == \
        ("legacy", "legacy", "Ritualist")
    assert (by_id[5]["season"], by_id[5]["rank_system"], by_id[5]["name"]) == \
        ("beta-season-1", "seasonal", "Mystic")
    assert by_id[5]["badge_url"].endswith("rank05_lg.png")


def test_seasons_list_newest_first_with_peaks(db):
    _seed_history(db)
    seasons = service.rank_timeline(db, ME, now=lambda: NOW)["seasons"]

    assert [s["slug"] for s in seasons] == ["beta-season-2", "beta-season-1", "legacy"]
    assert [s["is_current"] for s in seasons] == [True, False, False]
    s2, s1, legacy = seasons
    assert s2["peak"] is None                       # no ranked games in S2 yet
    assert s1["peak"]["badge"] == 55 and s1["peak"]["name"] == "Mystic"
    assert s1["peak"]["match_id"] == 5
    assert legacy["peak"] is None                   # legacy never gets a peak
    assert {"slug", "label", "rank_system", "started_at", "is_current", "peak"} == set(s1)


def test_peak_keeps_the_first_time_a_badge_was_reached(db):
    _load_seasonal(db)
    _insert_rank(db, 10, 40, "2026-08-10T00:00:00+00:00")
    _insert_rank(db, 11, 40, "2026-08-20T00:00:00+00:00")
    db.commit()
    s1 = next(s for s in service.rank_timeline(db, ME, now=lambda: NOW)["seasons"]
              if s["slug"] == "beta-season-1")
    assert s1["peak"]["match_id"] == 10


def test_a_season_that_has_not_started_is_not_listed(db):
    db.execute("INSERT INTO ranked_seasons(slug, label, rank_system, started_at)"
               " VALUES ('beta-season-3', 'Beta Season 3', 'seasonal', '2027-03-11T22:00:00+00:00')")
    db.commit()
    seasons = service.rank_timeline(db, ME, now=lambda: NOW)["seasons"]
    assert seasons[0]["slug"] == "beta-season-2" and seasons[0]["is_current"] is True


def test_current_rank_carries_its_season(db):
    _seed_history(db)
    rank = service.rank_timeline(db, ME, now=lambda: NOW)["current_rank"]
    assert (rank["badge"], rank["name"], rank["season"]) == (36, "Acolyte", "beta-season-1")


def test_profile_carries_current_rank_and_seasons(db):
    _seed_history(db)
    profile = service.player_profile(db, ME, now=lambda: NOW)
    assert profile["current_rank"]["season"] == "beta-season-1"
    assert [s["slug"] for s in profile["seasons"]] == ["beta-season-2", "beta-season-1", "legacy"]


def test_overview_carries_the_same_timeline(api_db):
    scope = make_scope()
    account_id = service.overview(api_db, scope)["account_id"]
    _load_seasonal(api_db)
    _insert_rank(api_db, 7, 45, "2026-11-07T01:35:26+00:00", account_id=account_id)
    api_db.commit()

    out = service.overview(api_db, scope, now=lambda: NOW)
    assert out["mmr_series"][-1]["name"] == "Sentinel"
    assert out["current_rank"]["season"] == "beta-season-2"
    s2 = out["seasons"][0]
    assert s2["slug"] == "beta-season-2" and s2["peak"]["badge"] == 45


# ── Migration 022 on a database whose ranks table holds one system ───────────

def _v21_db(tmp_path, ranks: list[tuple[int, str, str]], fetched_at: str):
    conn = connect(tmp_path / "v21.db")
    for step in _STEPS[:21]:
        conn.executescript(step.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 21")
    conn.executemany("INSERT INTO ranks(tier, name, color, fetched_at) VALUES (?, ?, ?, ?)",
                     [(t, n, c, fetched_at) for t, n, c in ranks])
    conn.commit()
    return conn


def _fixture_ranks(name: str) -> list[tuple[int, str, str]]:
    return [(r["tier"], r["name"], r["color"]) for r in load_fixture(name)]


def test_migration_keeps_seasonal_rows_and_seeds_legacy(tmp_path):
    seasonal = _fixture_ranks(SEASONAL_FIXTURE)
    conn = _v21_db(tmp_path, seasonal, "2026-11-09T23:23:06+00:00")

    migrate(conn)

    rows = {(r["rank_system"], r["tier"]): r for r in conn.execute("SELECT * FROM ranks")}
    assert len(rows) == 24
    # The live (seasonal) rows survive with their values AND their fetched_at.
    for tier, name, color in seasonal:
        row = rows[("seasonal", tier)]
        assert (row["name"], row["color"], row["fetched_at"]) == \
            (name, color, "2026-11-09T23:23:06+00:00")
    # The legacy ladder comes from the archived response (id 3551).
    assert rows[("legacy", 5)]["name"] == "Ritualist"
    assert rows[("legacy", 7)]["name"] == "Archon"


def test_migration_classifies_a_legacy_only_table_as_legacy(tmp_path):
    conn = _v21_db(tmp_path, _seasonal_to_legacy(), "2026-07-01T00:00:00+00:00")

    migrate(conn)

    systems = {r["rank_system"] for r in conn.execute("SELECT rank_system FROM ranks")}
    assert systems == {"legacy"}
    # The DB's own legacy rows were kept (INSERT OR IGNORE didn't replace them).
    fetched = {r["fetched_at"] for r in conn.execute("SELECT fetched_at FROM ranks")}
    assert fetched == {"2026-07-01T00:00:00+00:00"}


def _seasonal_to_legacy() -> list[tuple[int, str, str]]:
    legacy_names = {3: "Alchemist", 4: "Arcanist", 5: "Ritualist", 6: "Emissary", 7: "Archon"}
    return [(t, legacy_names.get(t, n), c) for t, n, c in _fixture_ranks(SEASONAL_FIXTURE)]
