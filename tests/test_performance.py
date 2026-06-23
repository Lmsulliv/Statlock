"""Continuous-metric performance: queries -> service -> endpoint + CLI.

The pure mean/interval/verdict math is covered in test_stats_continuous.py; these
tests exercise the assembly layer: per-hero and overall rows, the live population
baseline, the deaths direction-flip, the personal-only fallback, and the
API==CLI parity that proves both callers share one code path.

A dedicated fixture is needed because the presentation fixture (api_db) leaves
every continuous column NULL.
"""
from statistics import fmean

import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope
from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
    mean_verdict,
)
from stats import __main__ as cli
from tracker.db import connect
from tracker.migrate import migrate

ME = 1            # tracked self account
POP = 2           # a non-owner population player (excluded from the baseline)
WRAITH = 7        # the hero the owner AND the population play
SOLO = 8          # a hero ONLY the owner plays -> no population baseline
WHEN = "2026-06-15T12:00:00+00:00"
DUR = 600         # 10 minutes, so net_worth / min == net_worth / 10
BADGE = 50

# Owner vs population on Wraith. Owner farms more (net worth) and dies less.
OWNER_NW = [3000, 3100, 2900, 3050, 2950, 3000]   # /10 -> [300, 310, 290, 305, 295, 300]
OWNER_DEATHS = [2, 3, 2, 3, 2, 3]                  # mean 2.5 (low)
POP_NW = [2000, 2100, 1900, 2050, 1950, 2000]      # /10 -> mean 200
POP_DEATHS = [6, 7, 5, 6, 6, 6]                    # mean 6.0 (high)


def _player(slot, account, hero, team, won, *, net_worth=None, deaths=None):
    return {
        "player_slot": slot, "account_id": account, "hero_id": hero,
        "team": team, "won": won, "net_worth": net_worth, "deaths": deaths,
    }


def _add_match(conn, match_id, players):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, BADGE, BADGE, WHEN),
    )
    for p in players:
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
            " team, kills, deaths, assists, net_worth, last_hits, denies,"
            " player_damage, obj_damage, healing, won)"
            " VALUES (:mid, :player_slot, :account_id, :hero_id, :team, NULL,"
            " :deaths, NULL, :net_worth, NULL, NULL, NULL, NULL, NULL, :won)",
            {"mid": match_id, **p},
        )


def _seed(conn):
    for hid, name in ((WRAITH, "Wraith"), (SOLO, "Solo")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))

    # Six Wraith matches: owner (team 0) + a population Wraith (team 1).
    for i in range(6):
        _add_match(conn, 1000 + i, [
            _player(1, ME, WRAITH, 0, won=1, net_worth=OWNER_NW[i], deaths=OWNER_DEATHS[i]),
            _player(2, POP, WRAITH, 1, won=0, net_worth=POP_NW[i], deaths=POP_DEATHS[i]),
        ])
    # Five owner-only Solo matches: no other player ever pilots Solo -> no baseline.
    for i in range(5):
        _add_match(conn, 2000 + i,
                   [_player(1, ME, SOLO, 0, won=1, net_worth=1500 + i * 10, deaths=4)])
    conn.commit()


@pytest.fixture
def perf_db(tmp_path, monkeypatch):
    """A migrated DB seeded with continuous stats, exposed via DEADLOCK_DB so the
    FastAPI app and the CLI read the same data (mirrors conftest.api_db)."""
    path = tmp_path / "perf.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _metrics(row):
    return {m["key"]: m for m in row["metrics"]}


# ── Structure: overall first, then heroes A->Z ───────────────────────────────

def test_rows_are_overall_then_heroes_alphabetical(perf_db):
    rows = service.performance(perf_db, make_scope())
    assert [r["scope"] for r in rows] == ["overall", "hero", "hero"]
    assert rows[0]["hero_id"] is None and rows[0]["games"] == 11   # 6 Wraith + 5 Solo
    assert [r["hero_name"] for r in rows[1:]] == ["Solo", "Wraith"]


# ── Per-hero personal mean + live population baseline + verdict ───────────────

def test_net_worth_per_min_is_a_clear_strength_vs_population(perf_db):
    rows = service.performance(perf_db, make_scope())
    wraith = _metrics(next(r for r in rows if r["hero_name"] == "Wraith"))
    nw = wraith["net_worth_per_min"]

    assert nw["games"] == 6
    assert nw["mean"] == 300.0
    assert nw["baseline_mean"] == 200.0 == round(fmean([v / 10 for v in POP_NW]), 2)
    assert nw["baseline_games"] == 6              # population excludes the owner
    assert nw["delta"] == 100.0
    assert nw["verdict"] == VERDICT_CLEAR_STRENGTH
    # The interval is present and brackets the mean (exact bounds tested in stats).
    assert nw["ci_low"] is not None and nw["ci_high"] is not None
    assert nw["ci_low"] < nw["mean"] < nw["ci_high"]


def test_deaths_direction_is_flipped_so_fewer_reads_as_a_strength(perf_db):
    rows = service.performance(perf_db, make_scope())
    deaths = _metrics(next(r for r in rows if r["hero_name"] == "Wraith"))["deaths"]

    assert deaths["mean"] == 2.5 and deaths["baseline_mean"] == 6.0
    assert deaths["higher_is_better"] is False
    # Value-neutral, fewer-than-baseline deaths is a "weakness"; the service flips
    # it because for deaths lower is better, so the row reads as a strength.
    assert mean_verdict(OWNER_DEATHS, 6.0) == VERDICT_CLEAR_WEAKNESS
    assert deaths["verdict"] == VERDICT_CLEAR_STRENGTH


# ── Honest fallbacks: sparse metric and owner-only hero are personal-only ─────

def test_sparse_metric_is_personal_only(perf_db):
    # Healing is NULL for everyone in the fixture -> no personal sample and no
    # baseline, so it must show personal-only, never a comparison against nothing.
    healing = _metrics(next(r for r in service.performance(perf_db, make_scope())
                            if r["hero_name"] == "Wraith"))["healing"]
    assert healing["games"] == 0
    assert healing["mean"] is None
    assert healing["baseline_mean"] is None and healing["baseline_games"] == 0
    assert healing["verdict"] == VERDICT_NOT_ENOUGH_DATA


def test_owner_only_hero_has_no_baseline(perf_db):
    solo = _metrics(next(r for r in service.performance(perf_db, make_scope())
                         if r["hero_name"] == "Solo"))
    nw = solo["net_worth_per_min"]
    assert nw["mean"] is not None            # the owner has personal data...
    assert nw["baseline_mean"] is None       # ...but nobody else played Solo
    assert nw["baseline_games"] == 0
    assert nw["verdict"] == VERDICT_NOT_ENOUGH_DATA


# ── Two-caller rule: API JSON == service rows == CLI render ───────────────────

def test_api_and_cli_match_the_service(perf_db, capsys):
    scope = make_scope()
    rows = service.performance(perf_db, scope)

    api_rows = TestClient(app).get("/api/performance").json()
    assert api_rows == rows

    cli.main(["performance"])                 # reads the same DB via DEADLOCK_DB
    out = capsys.readouterr().out
    assert out.strip() == cli.render_performance(rows, scope).strip()


# ── Empty database renders an empty list, not an error ───────────────────────

def test_empty_database_returns_no_rows(empty_db_path):
    assert TestClient(app).get("/api/performance").json() == []
