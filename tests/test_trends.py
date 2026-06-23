"""Trends (performance over time): pure helpers -> queries -> service -> CLI/API.

Two layers are tested here:
  * the pure bucketing in stats.trends (calendar keys, week/month grouping,
    trailing rolling windows) -- no DB, deterministic;
  * the service assembly: win-rate + continuous-metric series, the honesty floor
    (thin buckets read not-enough-data), the live population baseline, and the
    API==CLI parity that proves both callers share one code path.

A dedicated fixture is needed because the presentation fixture (api_db) leaves
every continuous column NULL, and trends needs matches spread across time.
"""
import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope
from stats import VERDICT_FLOOR
from stats import __main__ as cli
from stats.trends import (
    TRENDS_WINDOW_DEFAULT,
    bucket_by_calendar,
    calendar_key,
    rolling_windows,
)
from tracker.db import connect
from tracker.migrate import migrate

ME = 1
POP = 2
WRAITH = 7
DUR = 600          # 10 min, so net_worth / min == net_worth / 10
BADGE = 50

# Week A: six matches in ISO week 2026-W25 (Mon 2026-06-15). Week B: three in
# the next week (Mon 2026-06-22) -- thin, below VERDICT_FLOOR.
WEEK_A = "2026-06-15"
WEEK_B = "2026-06-22"


# ── Pure helpers: calendar keys ──────────────────────────────────────────────

def test_calendar_key_week_label():
    # 2026-06-15 is a Monday; its ISO week is 2026-W25, spanning Mon 15 - Sun 21.
    assert calendar_key("2026-06-15T12:00:00+00:00", "week") == ("2026-W25", "Jun 15-21")


def test_calendar_key_week_spans_month_boundary():
    # ISO week 2026-W27 runs Mon Jun 29 - Sun Jul 5: label crosses the month.
    assert calendar_key("2026-06-29T09:00:00+00:00", "week") == ("2026-W27", "Jun 29-Jul 5")


def test_calendar_key_week_year_rollover():
    # ISO week 1 of 2026 starts Mon 2025-12-29 (the week holding the first Thu).
    key, _ = calendar_key("2025-12-29T00:00:00+00:00", "week")
    assert key == "2026-W01"
    # A Sunday in the same ISO week shares the key; the next Monday rolls over.
    assert calendar_key("2026-01-04T23:00:00+00:00", "week")[0] == "2026-W01"
    assert calendar_key("2026-01-05T00:00:00+00:00", "week")[0] == "2026-W02"


def test_calendar_key_month():
    assert calendar_key("2026-06-15T12:00:00+00:00", "month") == ("2026-06", "Jun 2026")


def test_calendar_key_rejects_unknown_granularity():
    with pytest.raises(ValueError):
        calendar_key("2026-06-15T12:00:00+00:00", "day")


# ── Pure helpers: grouping ───────────────────────────────────────────────────

def _items(*dates):
    return [{"start_time": f"{d}T12:00:00+00:00", "won": 1} for d in dates]


def test_bucket_by_calendar_groups_and_orders():
    # Out-of-order input still buckets chronologically by key.
    items = _items("2026-06-22", "2026-06-15", "2026-06-15", "2026-06-22", "2026-06-22")
    buckets = bucket_by_calendar(items, "week")
    assert [b["key"] for b in buckets] == ["2026-W25", "2026-W26"]
    assert [len(b["items"]) for b in buckets] == [2, 3]


def test_rolling_windows_trailing_membership():
    items = _items(*[f"2026-06-{d:02d}" for d in range(1, 6)])  # 5 items
    windows = rolling_windows(items, 3)
    # One point per match; the trailing window grows to the cap then slides.
    assert [len(w["items"]) for w in windows] == [1, 2, 3, 3, 3]
    # The last window holds exactly the final three matches.
    assert windows[-1]["items"] == items[-3:]


def test_rolling_windows_rejects_nonpositive_window():
    with pytest.raises(ValueError):
        rolling_windows(_items("2026-06-01"), 0)


# ── Service fixture: matches spread across two weeks, plus a population ───────

def _player(slot, account, hero, team, won, *, net_worth=None, deaths=None):
    return {"player_slot": slot, "account_id": account, "hero_id": hero,
            "team": team, "won": won, "net_worth": net_worth, "deaths": deaths}


def _add_match(conn, match_id, when, players):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, when, DUR, BADGE, BADGE, when),
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
    conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                 (WRAITH, "Wraith", WEEK_A))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WEEK_A))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WEEK_A))

    # Week A: 6 matches, owner wins 4. Owner farms ~3000 nw, population ~2000.
    a_wins = [1, 1, 1, 1, 0, 0]
    for i in range(6):
        when = f"{WEEK_A}T{12 + i}:00:00+00:00"
        _add_match(conn, 1000 + i, when, [
            _player(1, ME, WRAITH, 0, won=a_wins[i], net_worth=3000, deaths=2),
            _player(2, POP, WRAITH, 1, won=1 - a_wins[i], net_worth=2000, deaths=6),
        ])
    # Week B: 3 matches (thin), owner wins 1.
    b_wins = [1, 0, 0]
    for i in range(3):
        when = f"{WEEK_B}T{12 + i}:00:00+00:00"
        _add_match(conn, 2000 + i, when, [
            _player(1, ME, WRAITH, 0, won=b_wins[i], net_worth=3000, deaths=2),
            _player(2, POP, WRAITH, 1, won=1 - b_wins[i], net_worth=2000, deaths=6),
        ])
    conn.commit()


@pytest.fixture
def trends_db(tmp_path, monkeypatch):
    path = tmp_path / "trends.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _metric(result, key):
    return next(m for m in result["metrics"] if m["key"] == key)


# ── Service: shape + echoes ──────────────────────────────────────────────────

def test_metrics_lead_with_win_rate_then_perf_metrics(trends_db):
    result = service.trends(trends_db, make_scope(), mode="calendar", granularity="week")
    assert result["mode"] == "calendar" and result["granularity"] == "week"
    keys = [m["key"] for m in result["metrics"]]
    assert keys[0] == "win_rate"
    assert keys[1:] == ["net_worth_per_min", "kills", "deaths", "assists",
                        "last_hits", "denies", "player_damage", "obj_damage", "healing"]


# ── Service: calendar buckets + honesty floor ────────────────────────────────

def test_calendar_buckets_flag_thin_weeks(trends_db):
    wr = _metric(service.trends(trends_db, make_scope(),
                                mode="calendar", granularity="week"), "win_rate")
    points = wr["points"]
    assert [p["label"] for p in points] == ["Jun 15-21", "Jun 22-28"]
    week_a, week_b = points
    assert week_a["n"] == 6 and week_a["enough_data"] is True
    assert week_a["value"] == round(4 / 6, 4)
    # Wilson bounds are always present for a win-rate point.
    assert week_a["ci_low"] is not None and week_a["ci_high"] is not None
    # Week B has 3 games (< VERDICT_FLOOR == 5) -> not enough data.
    assert week_b["n"] == 3 and week_b["enough_data"] is False
    assert VERDICT_FLOOR == 5


def test_win_rate_baseline_is_the_accounts_overall_rate(trends_db):
    wr = _metric(service.trends(trends_db, make_scope(), mode="calendar"), "win_rate")
    assert wr["baseline"] == round(5 / 9, 4)   # 5 wins across 9 games


# ── Service: continuous metric series + live population baseline ──────────────

def test_continuous_metric_baseline_excludes_self(trends_db):
    nw = _metric(service.trends(trends_db, make_scope(),
                                mode="calendar", granularity="week"), "net_worth_per_min")
    # Population (POP) nets 2000 over 10 min == 200/min; the owner is excluded.
    assert nw["baseline"] == 200.0
    week_a = nw["points"][0]
    assert week_a["n"] == 6 and week_a["value"] == 300.0   # owner 3000/10
    assert week_a["enough_data"] is True


def test_deaths_carry_direction_metadata(trends_db):
    # Trends ships per-point values; direction (lower deaths is better) rides on
    # higher_is_better so the frontend can colour it -- the math stays neutral.
    deaths = _metric(service.trends(trends_db, make_scope()), "deaths")
    assert deaths["higher_is_better"] is False


# ── Service: rolling windows ─────────────────────────────────────────────────

def test_rolling_windows_apply_the_floor(trends_db):
    wr = _metric(service.trends(trends_db, make_scope(),
                                mode="rolling", window_games=5), "win_rate")
    # 9 matches -> 9 points; the first four trailing windows are below the floor.
    assert [p["enough_data"] for p in wr["points"]] == [False] * 4 + [True] * 5


def test_window_games_defaults(trends_db):
    result = service.trends(trends_db, make_scope())
    assert result["mode"] == "rolling"
    assert result["window_games"] == TRENDS_WINDOW_DEFAULT


# ── Two-caller rule: API JSON == service == CLI render ────────────────────────

def test_api_and_cli_match_the_service(trends_db, capsys):
    scope = make_scope()
    result = service.trends(trends_db, scope, mode="calendar", granularity="week")

    api_json = TestClient(app).get(
        "/api/trends?mode=calendar&granularity=week").json()
    assert api_json == result

    cli.main(["trends", "--mode", "calendar", "--granularity", "week"])
    out = capsys.readouterr().out
    assert out.strip() == cli.render_trends(result, scope).strip()


def test_empty_database_returns_no_metrics(empty_db_path):
    body = TestClient(app).get("/api/trends").json()
    assert body["metrics"] == []
