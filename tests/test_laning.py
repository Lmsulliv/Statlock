"""Early-game (laning) report: derivation -> ingest -> queries -> service -> API/CLI.

The pure lane-end snapshot picker is covered in test_stats_continuous_laning.py;
the mean/interval/verdict math in test_stats_continuous.py. These tests exercise
the laning-specific wiring: deriving the lane-end snapshot into laning_stats,
the same-transaction insert, the archive backfill, and the assembly layer
(per-hero/overall rows, the live population baseline at the mark, the personal-only
fallback, and the API==CLI parity that proves both callers share one code path).

Hard rule 3 (no live API) holds trivially -- every function here is a pure parse
or a local-SQLite read, and the autouse _no_network fixture blocks urlopen.
"""
from statistics import fmean

import pytest
from fastapi.testclient import TestClient

from api import queries, service
from api.app import app
from api.scope import make_scope
from ingest.parse import derive_laning_stats, insert_match, parse_metadata
from ingest.reprocess import reprocess_archive
from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
)
from stats import __main__ as cli
from tracker.db import connect
from tracker.migrate import migrate

ME = 1            # tracked self account
POP = 2           # a non-owner population player (excluded from the baseline)
WRAITH = 7        # the hero the owner AND the population play
SOLO = 8          # a hero ONLY the owner plays -> no population baseline
WHEN = "2026-06-15T12:00:00+00:00"
DUR = 1800
BADGE = 50

# Owner vs population net worth AT THE LANE-END MARK (raw, not per-minute). The
# owner wins lane comfortably; tight spread so the interval clears the baseline.
OWNER_NW = [5000, 5100, 4900, 5050, 4950, 5000]   # mean 5000
OWNER_LH = [40, 42, 38, 41, 39, 40]                # mean 40
POP_NW = [3000, 3100, 2900, 3050, 2950, 3000]      # mean 3000
POP_LH = [25, 26, 24, 25, 24, 26]                  # mean ~25


# ── Pure derivation: which snapshot becomes the laning_stats row ──────────────

def _meta_with_stats():
    """Two players with full stats[] series across the lane-end mark, plus one
    player whose match ended in laning (only an early snapshot) and one with no
    series at all."""
    def series(scale):
        # Cadence from api-findings: 180/360/540/720/900. Per-snapshot last_hits is
        # null in the real payload; creep_kills is the proxy we read.
        return [{"time_stamp_s": t, "net_worth": scale * t,
                 "creep_kills": t // 10, "denies": t // 100, "last_hits": None}
                for t in (180, 360, 540, 720, 900)]
    return {"match_info": {
        "match_id": 4242, "start_time": 0, "duration_s": DUR, "game_mode": 1,
        "winning_team": 0, "average_badge_team0": BADGE, "average_badge_team1": BADGE,
        "players": [
            {"player_slot": 1, "account_id": ME, "hero_id": WRAITH, "team": 0,
             "stats": series(10)},
            {"player_slot": 2, "account_id": POP, "hero_id": WRAITH, "team": 1,
             "stats": series(5)},
            {"player_slot": 3, "account_id": 3, "hero_id": WRAITH, "team": 0,
             "stats": [{"time_stamp_s": 180, "net_worth": 999, "creep_kills": 9,
                        "denies": 1, "last_hits": None}]},
            {"player_slot": 4, "account_id": 4, "hero_id": WRAITH, "team": 1,
             "stats": []},
        ],
    }}


def test_derive_picks_lane_end_snapshot_and_reads_creep_kills():
    rows = derive_laning_stats(_meta_with_stats())
    by_slot = {r[1]: r for r in rows}
    # Slots 1 and 2 reach the mark; slot 3 (short) still gets its last snapshot;
    # slot 4 (empty series) is skipped entirely -- no fabricated zero row.
    assert set(by_slot) == {1, 2, 3}

    # (match_id, player_slot, net_worth, last_hits, denies, sampled_at_s)
    me = by_slot[1]
    assert me[0] == 4242 and me[5] == 540          # latest snapshot <= 600 s
    assert me[2] == 10 * 540                        # net_worth at that snapshot
    assert me[3] == 540 // 10                        # last_hits = creep_kills
    assert me[4] == 540 // 100                        # denies

    short = by_slot[3]
    assert short[5] == 180 and short[2] == 999       # short match: last snapshot kept


def test_derive_empty_payload_yields_no_rows():
    assert derive_laning_stats({}) == []


# ── Same-transaction insert + archive backfill ───────────────────────────────

def _seed_heroes(conn, *hero_ids):
    for hid in hero_ids:
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, 't')",
                     (hid, f"Hero{hid}"))
    conn.commit()


def test_insert_match_writes_laning_stats(db):
    _seed_heroes(db, WRAITH)
    meta = _meta_with_stats()
    import json
    parsed = parse_metadata(meta, json.dumps(meta), set(), None, WHEN)
    with db:
        insert_match(db, parsed)
    rows = db.execute("SELECT player_slot, net_worth, sampled_at_s FROM laning_stats"
                      " WHERE match_id = 4242 ORDER BY player_slot").fetchall()
    assert [r["player_slot"] for r in rows] == [1, 2, 3]
    assert dict(rows[0])["sampled_at_s"] == 540


def test_reprocess_archive_is_idempotent_for_laning(db):
    import json
    _seed_heroes(db, WRAITH)
    meta = _meta_with_stats()
    body = json.dumps(meta)
    parsed = parse_metadata(meta, body, set(), None, WHEN)
    with db:
        insert_match(db, parsed)
    db.execute("INSERT INTO raw_api_responses(url, status_code, body, fetched_at)"
               " VALUES (?, 200, ?, ?)",
               ("https://api.deadlock-api.com/v1/matches/4242/metadata", body, WHEN))
    db.commit()

    def count():
        return db.execute("SELECT COUNT(*) FROM laning_stats WHERE match_id = 4242"
                          ).fetchone()[0]

    result = reprocess_archive(db)
    after_first = count()
    reprocess_archive(db)
    after_second = count()
    assert after_first == 3 == after_second          # delete-then-insert holds steady
    assert result["laning_rows_rebuilt"] == 3


# ── Service assembly: per-hero/overall, baseline at the mark, verdict ─────────

def _add_match(conn, match_id, players):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, BADGE, BADGE, WHEN),
    )
    for slot, account, hero, team, nw, lh in players:
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
            " team, won) VALUES (?, ?, ?, ?, ?, 1)",
            (match_id, slot, account, hero, team),
        )
        conn.execute(
            "INSERT INTO laning_stats(match_id, player_slot, net_worth, last_hits,"
            " denies, sampled_at_s) VALUES (?, ?, ?, ?, 5, 540)",
            (match_id, slot, nw, lh),
        )


def _seed(conn):
    for hid, name in ((WRAITH, "Wraith"), (SOLO, "Solo")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))
    for i in range(6):
        _add_match(conn, 1000 + i, [
            (1, ME, WRAITH, 0, OWNER_NW[i], OWNER_LH[i]),
            (2, POP, WRAITH, 1, POP_NW[i], POP_LH[i]),
        ])
    for i in range(5):
        _add_match(conn, 2000 + i, [(1, ME, SOLO, 0, 2500 + i * 10, 20)])
    conn.commit()


@pytest.fixture
def laning_db(tmp_path, monkeypatch):
    path = tmp_path / "laning.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _metrics(row):
    return {m["key"]: m for m in row["metrics"]}


def test_rows_are_overall_then_heroes_alphabetical(laning_db):
    rows = service.laning(laning_db, make_scope())
    assert [r["scope"] for r in rows] == ["overall", "hero", "hero"]
    assert rows[0]["hero_id"] is None and rows[0]["games"] == 11   # 6 Wraith + 5 Solo
    assert [r["hero_name"] for r in rows[1:]] == ["Solo", "Wraith"]


def test_net_worth_at_lane_end_is_a_clear_strength(laning_db):
    wraith = _metrics(next(r for r in service.laning(laning_db, make_scope())
                           if r["hero_name"] == "Wraith"))
    nw = wraith["net_worth"]
    assert nw["games"] == 6
    assert nw["mean"] == 5000.0
    assert nw["baseline_mean"] == 3000.0 == round(fmean(POP_NW), 2)
    assert nw["baseline_games"] == 6              # population excludes the owner
    assert nw["delta"] == 2000.0
    assert nw["verdict"] == VERDICT_CLEAR_STRENGTH


def test_owner_only_hero_has_no_baseline(laning_db):
    solo = _metrics(next(r for r in service.laning(laning_db, make_scope())
                         if r["hero_name"] == "Solo"))["net_worth"]
    assert solo["mean"] is not None            # the owner has personal data...
    assert solo["baseline_mean"] is None       # ...but nobody else played Solo
    assert solo["verdict"] == VERDICT_NOT_ENOUGH_DATA


def test_api_and_cli_match_the_service(laning_db, capsys):
    scope = make_scope()
    rows = service.laning(laning_db, scope)

    api_rows = TestClient(app).get("/api/laning").json()
    assert api_rows == rows

    cli.main(["laning"])                          # reads the same DB via DEADLOCK_DB
    out = capsys.readouterr().out
    assert out.strip() == cli.render_laning(rows, scope).strip()


def test_empty_database_returns_no_rows(empty_db_path):
    assert TestClient(app).get("/api/laning").json() == []


# ── Lane deaths (kill_events derived, lane-pair-opponent rule) ────────────────
#
# A lane death = a kill_event where you are the victim, the killer is a lane-pair
# opponent (opposite team, same (lane+1)/2 pairing), and game_time_s <= LANE_END_S.
# These tests seed kill_events + match_players.lane directly (the rest of the
# laning suite leaves both untouched, so the new metric is 0 there and the existing
# assertions stay green).

# ME laned at slot 1 / team 0 / lane 1, so (lane+1)/2 = 1. Slot 2 (team 1, lane 2)
# is the lane-pair opponent; slot 3 (team 1, lane 3 -> pair 2) and slot 4 (team 0,
# my own team) are NOT, and a NULL killer is a tower/creep.
ME_SLOT = 1
PAIR_OPP = 2          # lane-pair opponent: counts
OTHER_LANE_OPP = 3    # opponent in a different lane pair: excluded (strict rule)
TEAMMATE = 4          # same team: excluded


def _matchrow(conn, match_id):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, BADGE, BADGE, WHEN),
    )


def _player(conn, match_id, slot, account, hero, team, lane):
    conn.execute(
        "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
        " team, lane, won) VALUES (?, ?, ?, ?, ?, ?, 1)",
        (match_id, slot, account, hero, team, lane),
    )


def _laning_row(conn, match_id, slot):
    conn.execute(
        "INSERT INTO laning_stats(match_id, player_slot, net_worth, last_hits,"
        " denies, sampled_at_s) VALUES (?, ?, 4000, 30, 5, 540)",
        (match_id, slot),
    )


def _kill(conn, match_id, victim, killer, t):
    conn.execute(
        "INSERT INTO kill_events(match_id, game_time_s, victim_slot, killer_slot)"
        " VALUES (?, ?, ?, ?)",
        (match_id, t, victim, killer),
    )


def _me_match(conn, match_id, kills):
    """One match ME played on Wraith (slot 1), with the lane-pair opponent and the
    two non-qualifying killers always present so any kill can be attributed. `kills`
    is a list of (killer_slot, game_time_s) kill_events with ME as the victim."""
    _matchrow(conn, match_id)
    _player(conn, match_id, ME_SLOT, ME, WRAITH, 0, 1)
    _player(conn, match_id, PAIR_OPP, 20, WRAITH, 1, 2)
    _player(conn, match_id, OTHER_LANE_OPP, 30, WRAITH, 1, 3)
    _player(conn, match_id, TEAMMATE, 40, WRAITH, 0, 2)
    _laning_row(conn, match_id, ME_SLOT)            # ME has a lane-end snapshot
    for killer, t in kills:
        _kill(conn, match_id, ME_SLOT, killer, t)


@pytest.fixture
def lane_deaths_db(tmp_path, monkeypatch):
    """ME plays 6 Wraith matches; per-match qualifying lane-death counts are
    [2, 0, 1, 1, 1, 1] (sum 6 -> mean 1.0). The 0-match carries only EXCLUDED
    events (post-laning, non-lane-pair killer, teammate, tower) to prove a played
    match with no qualifying death contributes a real 0, not NULL. A small,
    low-death population gives an honest baseline ME clearly exceeds."""
    path = tmp_path / "lanedeaths.db"
    conn = connect(path)
    migrate(conn)
    conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, 'Wraith', ?)",
                 (WRAITH, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))

    _me_match(conn, 7001, [(PAIR_OPP, 200), (PAIR_OPP, 590)])      # 2 qualifying
    _me_match(conn, 7002, [                                         # 0 qualifying:
        (PAIR_OPP, 700),          # post-laning (> LANE_END_S)
        (OTHER_LANE_OPP, 200),    # opponent in a different lane pair
        (TEAMMATE, 200),          # same team
        (None, 100),              # tower / creep (NULL killer_slot)
    ])
    _me_match(conn, 7003, [(PAIR_OPP, 540)])                        # 1
    _me_match(conn, 7004, [(PAIR_OPP, 300)])                        # 1
    _me_match(conn, 7005, [(PAIR_OPP, 450)])                        # 1
    _me_match(conn, 7006, [(PAIR_OPP, 120)])                        # 1

    # Population (excluded from ME's rows, included in the baseline): 5 Wraith
    # laning rows, only the first with a qualifying lane death -> baseline 0.2.
    for i, deaths in enumerate([1, 0, 0, 0, 0]):
        mid = 8000 + i
        _matchrow(conn, mid)
        _player(conn, mid, 1, 100 + i, WRAITH, 0, 1)               # a population player
        _player(conn, mid, 2, 200 + i, WRAITH, 1, 2)               # their lane-pair opp
        _laning_row(conn, mid, 1)
        if deaths:
            _kill(conn, mid, 1, 2, 300)

    conn.commit()
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def test_personal_lane_deaths_counts_only_qualifying(lane_deaths_db):
    # personal_laning returns one row per ME match; lane_deaths is the per-match
    # qualifying count. Post-laning, non-lane-pair, teammate and tower kills are all
    # excluded, so the 0-match reads a real 0 (present, not dropped).
    rows = queries.personal_laning(lane_deaths_db, make_scope(account_id=ME))
    assert sorted(r["lane_deaths"] for r in rows) == [0, 1, 1, 1, 1, 2]


def test_lane_deaths_per_game_average_and_direction(lane_deaths_db):
    wraith = _metrics(next(r for r in service.laning(lane_deaths_db, make_scope())
                           if r["hero_name"] == "Wraith"))
    ld = wraith["lane_deaths"]
    assert ld["games"] == 6
    assert ld["mean"] == 1.0                       # (2 + 0 + 1 + 1 + 1 + 1) / 6
    assert ld["higher_is_better"] is False         # fewer lane deaths is better


def test_lane_deaths_baseline_is_population_and_flips_to_weakness(lane_deaths_db):
    wraith = _metrics(next(r for r in service.laning(lane_deaths_db, make_scope())
                           if r["hero_name"] == "Wraith"))
    ld = wraith["lane_deaths"]
    # Honest population baseline computed the same way, owner excluded.
    assert ld["baseline_mean"] == 0.2              # 1 lane death over 5 pop games
    assert ld["baseline_games"] == 5
    assert ld["delta"] == 0.8                      # 1.0 - 0.2, dying more than the field
    # higher_is_better is False, so more deaths than the field is a WEAKNESS.
    assert ld["verdict"] in (VERDICT_CLEAR_WEAKNESS, VERDICT_LEANING_WEAKNESS)


def test_lane_deaths_overall_pools_played_heroes(lane_deaths_db):
    overall = _metrics(next(r for r in service.laning(lane_deaths_db, make_scope())
                            if r["scope"] == "overall"))
    # ME only played Wraith, so the overall lane-death mean matches the hero row.
    assert overall["lane_deaths"]["mean"] == 1.0
