"""Provisional history summaries: v_account_matches, the rewired personal reads,
per-hero records, and the onboarding progress endpoint.

The story these tests pin down: a freshly imported account renders real numbers
from discovery's match-history summaries (flagged provisional), and once the
drain loop ingests full metadata the SAME endpoints gain damage metrics, the flag
flips false, and no match is ever double-counted. Era/badge-narrowed scopes drop
the summaries honestly (their NULL columns fail the predicates); the population
baseline never rests on a summary.
"""
import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope, Scope
from stats import VERDICT_NOT_ENOUGH_DATA
from tracker.db import connect
from tracker.migrate import migrate

ME = 1                 # tracked self account
POP = 2                # a population player (never the owner)
WRAITH, ABRAMS = 7, 8  # heroes the owner plays
WHEN = "2026-06-15T12:00:00+00:00"   # in the latest curated era
DUR = 600              # 10 min, so net_worth/min == net_worth/10


def _summary(conn, account_id, match_id, hero, *, won=1, kills=5, net_worth=3000,
             duration_s=DUR, game_mode="1", start=WHEN):
    """A discovery summary row: hero/K-D-A/net worth known, damage/era/badge not."""
    conn.execute(
        "INSERT INTO account_match_summaries(account_id, match_id, hero_id,"
        " start_time, game_mode, won, kills, deaths, assists, net_worth,"
        " last_hits, denies, duration_s, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 3, 7, ?, 40, 2, ?, ?)",
        (account_id, match_id, hero, start, game_mode, won, kills, net_worth,
         duration_s, WHEN),
    )


def _full(conn, match_id, players, *, era_id=None, badge=50, game_mode="1",
          start=WHEN, duration_s=DUR):
    """Full metadata: a matches row + its match_players. `players` is a list of
    (slot, account_id, hero, team, won, net_worth, damage_taken) tuples."""
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, '{}', ?)",
        (match_id, start, duration_s, game_mode, era_id, badge, badge, WHEN),
    )
    for slot, account, hero, team, won, nw, dmg_taken in players:
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
            " team, kills, deaths, assists, net_worth, last_hits, denies,"
            " player_damage, obj_damage, healing, player_damage_taken, won)"
            " VALUES (?, ?, ?, ?, ?, 5, 3, 7, ?, 40, 2, 1000, 500, 0, ?, ?)",
            (match_id, slot, account, hero, team, nw, dmg_taken, won),
        )


def _base(conn):
    for hid, name in ((WRAITH, "Wraith"), (ABRAMS, "Abrams")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))
    conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A migrated DB with the owner registered, exposed via DEADLOCK_DB so the
    FastAPI app reads the same file."""
    path = tmp_path / "prov.db"
    conn = connect(path)
    migrate(conn)
    _base(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


# ── Scenario 1: summaries only -> real numbers, flagged provisional ───────────

def test_summaries_only_render_flagged_provisional(db):
    for i in range(6):
        _summary(db, ME, 1000 + i, WRAITH, won=(i < 4))
    db.commit()
    scope = make_scope()

    perf = service.performance(db, scope)
    assert perf["provisional"] is True
    overall = next(r for r in perf["rows"] if r["scope"] == "overall")
    metrics = {m["key"]: m for m in overall["metrics"]}
    # Net worth/min is known from the summary (3000/10 = 300)...
    assert metrics["net_worth_per_min"]["mean"] == 300.0
    # ...but damage-taken has no summary source, so it stays personal-only, NEVER 0.
    assert metrics["player_damage_taken"]["mean"] is None
    assert metrics["player_damage_taken"]["games"] == 0

    hero_rec = service.hero_records(db, scope)
    assert hero_rec["provisional"] is True
    wraith = next(h for h in hero_rec["heroes"] if h["hero_name"] == "Wraith")
    assert wraith["games"] == 6 and wraith["wins"] == 4 and wraith["provisional"] is True

    assert service.tilt(db, scope)["provisional"] is True
    assert service.trends(db, scope)["provisional"] is True
    assert service.overview(db, scope)["provisional"] is True


# ── Scenario 2: metadata upgrade -> damage appears, flag flips, no double count ─

def test_metadata_upgrade_flips_flag_without_double_counting(db):
    for i in range(6):
        _summary(db, ME, 1000 + i, WRAITH, won=(i < 4))
    db.commit()

    before = service.hero_records(db, make_scope())["heroes"][0]
    assert before["games"] == 6

    # Ingest full metadata for the SAME six matches (owner on team 0).
    for i in range(6):
        _full(db, 1000 + i, [(1, ME, WRAITH, 0, 1 if i < 4 else 0, 3000, 40000)])
    db.commit()

    perf = service.performance(db, make_scope())
    assert perf["provisional"] is False                     # flag flipped
    overall = next(r for r in perf["rows"] if r["scope"] == "overall")
    metrics = {m["key"]: m for m in overall["metrics"]}
    assert metrics["player_damage_taken"]["mean"] == 40000.0  # damage now present
    assert overall["games"] == 6                            # NOT 12: no double count

    after = service.hero_records(db, make_scope())["heroes"][0]
    assert after["games"] == 6 and after["provisional"] is False


# ── Scenario 3: narrowed scope excludes summaries; all-time full-range keeps them ─

def test_era_scope_excludes_summaries(db):
    _summary(db, ME, 1000, WRAITH)                          # era_id NULL on the view
    _full(db, 2000, [(1, ME, WRAITH, 0, 1, 3000, 40000)], era_id=5)
    db.commit()

    all_time = service.hero_records(db, make_scope())["heroes"][0]
    assert all_time["games"] == 2                           # summary + full

    era5 = service.hero_records(db, Scope(era_ids=(5,)))["heroes"][0]
    assert era5["games"] == 1                               # summary dropped (NULL era)
    assert era5["provisional"] is False


def test_badge_narrowed_scope_excludes_summaries(db):
    _summary(db, ME, 1000, WRAITH)                          # badge NULL on the view
    _full(db, 2000, [(1, ME, WRAITH, 0, 1, 3000, 40000)], badge=50)
    db.commit()

    # A narrowed badge range (30-59) has a real badge predicate; the summary's NULL
    # badge fails BETWEEN and drops out, while the full row at badge 50 stays.
    narrowed = service.hero_records(db, Scope(badge_min=30, badge_max=59))
    assert narrowed["heroes"][0]["games"] == 1
    assert narrowed["provisional"] is False


# ── Scenario 4: NULL won never scores; NULL duration still counts kills ────────

def test_unknown_result_summary_never_feeds_winrate(db):
    _summary(db, ME, 1000, WRAITH, won=1)
    db.execute("UPDATE account_match_summaries SET won = NULL WHERE match_id = 1000")
    _summary(db, ME, 1001, WRAITH, won=0)
    db.commit()

    hero = service.hero_records(db, make_scope())["heroes"][0]
    assert hero["games"] == 1 and hero["wins"] == 0         # the NULL-won row is dropped
    # Tilt's session stream likewise never counts an unknown result.
    assert service.tilt(db, make_scope())["overall"]["games"] == 1


def test_unknown_duration_summary_counts_kills_but_not_net_worth_per_min(db):
    _summary(db, ME, 1000, WRAITH, net_worth=3000, duration_s=None)
    db.commit()

    overall = next(r for r in service.performance(db, make_scope())["rows"]
                   if r["scope"] == "overall")
    metrics = {m["key"]: m for m in overall["metrics"]}
    assert metrics["kills"]["mean"] == 5.0                  # kills known
    assert metrics["net_worth_per_min"]["mean"] is None     # net worth / NULL duration


# ── Scenario 5: the population baseline never rests on a summary ───────────────

def test_baseline_ignores_summary_rows(db):
    # The owner plays Wraith with real metadata; the population player POP has ONE
    # real Wraith game plus a summary that must not enter the baseline.
    _full(db, 1000, [(1, ME, WRAITH, 0, 1, 3000, 40000),
                     (2, POP, WRAITH, 1, 0, 2000, 60000)])
    _summary(db, POP, 5000, WRAITH, net_worth=9999)         # POP's summary
    db.commit()

    overall = next(r for r in service.performance(db, make_scope())["rows"]
                   if r["scope"] == "overall")
    nw = {m["key"]: m for m in overall["metrics"]}["net_worth_per_min"]
    # Baseline is POP's single FULL Wraith game (2000/10 = 200); the 9999 summary
    # is excluded, and the population game count is 1, not 2.
    assert nw["baseline_mean"] == 200.0 and nw["baseline_games"] == 1


# ── Scenario 6: recurring players' self-baseline stays full-only ──────────────

def test_recurring_self_baseline_is_full_only(db):
    # One full match (owner + a co-player) and one owner-only summary. Recurring's
    # own win-rate baseline must count only the full match -- it can't see
    # co-players from a summary, so folding the summary in would desync the two.
    _full(db, 1000, [(1, ME, WRAITH, 0, 1, 3000, 40000),
                     (2, POP, ABRAMS, 0, 1, 2500, 30000)])
    _summary(db, ME, 5000, WRAITH, won=0)
    db.commit()

    rec = service.recurring_players(db, make_scope())
    assert rec["overall"]["games"] == 1                     # summary excluded
    # Tilt, by contrast, DOES see the summary (2 games), proving the split is real.
    assert service.tilt(db, make_scope())["overall"]["games"] == 2


# ── Progress endpoint: counts move as the queue drains ────────────────────────

def test_progress_counts_track_the_drain(db):
    # Discovery has materialized three summaries and queued the matches.
    for i in range(3):
        _summary(db, ME, 1000 + i, WRAITH)
        db.execute(
            "INSERT INTO fetch_queue(match_id, discovered_at, status, priority,"
            " discovered_for_account) VALUES (?, ?, 'pending', 1, ?)",
            (1000 + i, WHEN, ME))
    db.commit()

    p = service.account_progress(db, ME)
    assert p == {"account_id": ME, "known": 3, "analyzed": 0,
                 "prioritized_pending": 3, "backfill_pending": 0, "deferred": 0,
                 "unavailable": 0}

    # The drain loop fetches one match: full metadata lands, its queue row flips.
    _full(db, 1000, [(1, ME, WRAITH, 0, 1, 3000, 40000)])
    db.execute("UPDATE fetch_queue SET status = 'fetched' WHERE match_id = 1000")
    db.commit()

    p = service.account_progress(db, ME)
    assert p["analyzed"] == 1 and p["prioritized_pending"] == 2
    assert p["known"] == 3                                  # summaries are still held

    # A match the API can't serve (five 404s) parks as 'unavailable'; the
    # progress payload surfaces it so the UI can hint at the ingest tool.
    db.execute("UPDATE fetch_queue SET status = 'unavailable' WHERE match_id = 1001")
    db.commit()

    p = service.account_progress(db, ME)
    assert p["unavailable"] == 1 and p["prioritized_pending"] == 1


# ── Endpoint wiring (the two new routes exist and carry the flag) ─────────────

def test_new_endpoints_are_wired(db):
    _summary(db, ME, 1000, WRAITH)
    db.commit()
    client = TestClient(app)

    hero_rec = client.get("/api/hero-records").json()
    assert hero_rec["provisional"] is True and len(hero_rec["heroes"]) == 1

    prog = client.get(f"/api/accounts/{ME}/progress").json()
    assert prog["known"] == 1 and prog["analyzed"] == 0


# ── Empty account: hero-records and progress stay calm ────────────────────────

def test_empty_account_hero_records_and_progress(db):
    assert service.hero_records(db, make_scope()) == {"provisional": False, "heroes": []}
    # No matches known yet, no queue rows -> all zeros, no error.
    assert service.account_progress(db, ME) == {
        "account_id": ME, "known": 0, "analyzed": 0,
        "prioritized_pending": 0, "backfill_pending": 0, "deferred": 0,
        "unavailable": 0}


def test_not_enough_data_verdict_on_thin_hero(db):
    # A single summary game is below the verdict floor: a hero record must never
    # claim a verdict off one game, exactly like every other screen.
    _summary(db, ME, 1000, WRAITH)
    db.commit()
    hero = service.hero_records(db, make_scope())["heroes"][0]
    assert hero["verdict"] == VERDICT_NOT_ENOUGH_DATA
