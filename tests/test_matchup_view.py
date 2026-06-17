"""Tests: v_my_matchups view correctness on a hand-computed example.

Three fake matches with 2 players per team (keeps hand-verification easy;
the view logic is team-membership-based, so 2v2 and 6v6 exercise the same
SQL path).

Players:
  account 100 (is_self=1) — always plays hero 1
  account 200 (bystander, is_self=0) — plays hero 6, present only in Match A

Two patch eras: era 1 and era 2.

Match A (era 1): acct 100 hero 1 team 0 WON, vs heroes 2+3 on team 1.
                 Teammate: acct 200 hero 6 on team 0.
Match B (era 1): acct 100 hero 1 team 1 LOST, vs heroes 2+4 on team 0.
                 Teammate: hero 6 (different account, not tracked).
Match C (era 2): acct 100 hero 1 team 0 WON, vs heroes 2+3 on team 1.
                 Teammate: hero 6.

Expected v_my_matchups for account 100:
  (my_hero=1, enemy_hero=2, era_id=1, games=2, wins=1)  — A won, B lost
  (my_hero=1, enemy_hero=3, era_id=1, games=1, wins=1)  — A won
  (my_hero=1, enemy_hero=4, era_id=1, games=1, wins=0)  — B lost
  (my_hero=1, enemy_hero=2, era_id=2, games=1, wins=1)  — C won
  (my_hero=1, enemy_hero=3, era_id=2, games=1, wins=1)  — C won

Hero 6 (teammate) must never appear; account 200 (bystander) must produce
no rows.
"""
import pytest

from api import queries
from api.scope import make_scope


def _insert_hero(db, hero_id: int) -> None:
    db.execute(
        "INSERT OR IGNORE INTO heroes(hero_id,name,fetched_at) VALUES(?,?,?)",
        (hero_id, f"Hero{hero_id}", "2026-01-01T00:00:00Z"),
    )


def _insert_match(db, match_id: int, era_id: int, winning_team: int) -> None:
    db.execute(
        """INSERT INTO matches
           (match_id,start_time,duration_s,winning_team,era_id,raw_json,ingested_at)
           VALUES (?,?,1800,?,?,'{}','2026-01-01T00:00:00Z')""",
        (match_id, f"2026-01-0{match_id}T00:00:00Z", winning_team, era_id),
    )


def _insert_player(db, match_id: int, player_slot: int, account_id: int,
                   hero_id: int, team: int, won: int) -> None:
    db.execute(
        """INSERT INTO match_players
           (match_id,player_slot,account_id,hero_id,team,won)
           VALUES (?,?,?,?,?,?)""",
        (match_id, player_slot, account_id, hero_id, team, won),
    )


@pytest.fixture
def seeded_db(db):
    """Database populated with the three-match hand-computed example."""
    # ── hero reference rows ──────────────────────────────────────────────────
    for hid in (1, 2, 3, 4, 6):
        _insert_hero(db, hid)

    # ── patch eras ───────────────────────────────────────────────────────────
    db.execute(
        "INSERT INTO patch_eras(era_id,label,started_at) VALUES(1,'Era1','2026-01-01T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO patch_eras(era_id,label,started_at) VALUES(2,'Era2','2026-04-01T00:00:00Z')"
    )

    # ── tracked accounts ─────────────────────────────────────────────────────
    db.execute(
        "INSERT INTO tracked_accounts VALUES(100,'Self',1,'2026-01-01T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO tracked_accounts VALUES(200,'Bystander',0,'2026-01-01T00:00:00Z')"
    )

    # ── Match A: era 1, team 0 wins ──────────────────────────────────────────
    _insert_match(db, 1, 1, 0)
    _insert_player(db, 1, 1, 100, 1, 0, 1)   # self — hero 1, won
    _insert_player(db, 1, 2, 200, 6, 0, 1)   # bystander — hero 6, won (same team)
    _insert_player(db, 1, 3, 301, 2, 1, 0)   # enemy hero 2
    _insert_player(db, 1, 4, 302, 3, 1, 0)   # enemy hero 3

    # ── Match B: era 1, team 0 wins (self on team 1 → loss) ─────────────────
    _insert_match(db, 2, 1, 0)
    _insert_player(db, 2, 1, 100, 1, 1, 0)   # self — hero 1, lost (team 0 wins)
    _insert_player(db, 2, 2, 303, 6, 1, 0)   # teammate hero 6 (different account, lost)
    _insert_player(db, 2, 3, 304, 2, 0, 1)   # enemy hero 2 (won, team 0)
    _insert_player(db, 2, 4, 305, 4, 0, 1)   # enemy hero 4 (won, team 0)

    # ── Match C: era 2, team 0 wins ──────────────────────────────────────────
    _insert_match(db, 3, 2, 0)
    _insert_player(db, 3, 1, 100, 1, 0, 1)   # self — hero 1, won
    _insert_player(db, 3, 2, 306, 6, 0, 1)   # teammate hero 6
    _insert_player(db, 3, 3, 307, 2, 1, 0)   # enemy hero 2
    _insert_player(db, 3, 4, 308, 3, 1, 0)   # enemy hero 3

    db.commit()
    return db


def _fetch_matchups(db) -> dict[tuple, dict]:
    """Return view rows keyed by (my_hero, enemy_hero, era_id)."""
    rows = db.execute(
        "SELECT account_id, my_hero, enemy_hero, era_id, games, wins FROM v_my_matchups"
    ).fetchall()
    return {
        (r["my_hero"], r["enemy_hero"], r["era_id"]): dict(r) for r in rows
    }


def test_exactly_five_rows_for_self(seeded_db):
    rows = seeded_db.execute("SELECT * FROM v_my_matchups").fetchall()
    assert len(rows) == 5


def test_bystander_produces_no_rows(seeded_db):
    rows = seeded_db.execute(
        "SELECT * FROM v_my_matchups WHERE account_id = 200"
    ).fetchall()
    assert len(rows) == 0


def test_teammate_hero_never_appears(seeded_db):
    rows = seeded_db.execute(
        "SELECT * FROM v_my_matchups WHERE enemy_hero = 6"
    ).fetchall()
    assert len(rows) == 0


def test_era1_hero2_two_games_one_win(seeded_db):
    matchups = _fetch_matchups(seeded_db)
    row = matchups[(1, 2, 1)]
    assert row["games"] == 2
    assert row["wins"] == 1


def test_era1_hero3_one_game_one_win(seeded_db):
    matchups = _fetch_matchups(seeded_db)
    row = matchups[(1, 3, 1)]
    assert row["games"] == 1
    assert row["wins"] == 1


def test_era1_hero4_one_game_zero_wins(seeded_db):
    matchups = _fetch_matchups(seeded_db)
    # Match B: winning_team=0, self is team 1 → lost. Hero 4 is on winning team 0.
    row = matchups[(1, 4, 1)]
    assert row["games"] == 1
    assert row["wins"] == 0


def test_era2_hero2_one_game_one_win(seeded_db):
    matchups = _fetch_matchups(seeded_db)
    row = matchups[(1, 2, 2)]
    assert row["games"] == 1
    assert row["wins"] == 1


def test_era2_hero3_one_game_one_win(seeded_db):
    matchups = _fetch_matchups(seeded_db)
    row = matchups[(1, 3, 2)]
    assert row["games"] == 1
    assert row["wins"] == 1


def test_matchups_count_anonymized_opponents(db):
    """An anonymized opponent (account_id 0) still piloted a known hero, so it
    must count toward matchups -- hero identity is what's aggregated here, and
    that is never anonymized. (Contrast recurring co-players, which exclude 0.)"""
    db.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (7, 'Wraith', 't')")
    db.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (15, 'Bebop', 't')")
    db.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at) VALUES (1, 1, 't')")
    for mid in (800, 801, 802):
        db.execute(
            "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
            " winning_team, raw_json, ingested_at)"
            " VALUES (?, '2026-06-15T12:00:00+00:00', 1800, '1', 0, '{}', 't')", (mid,))
        db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                   " VALUES (?, 1, 1, 7, 0, 1)", (mid,))     # me on hero 7, won
        db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                   " VALUES (?, 2, 0, 15, 1, 0)", (mid,))    # anonymized enemy on hero 15
    db.commit()

    rows = {(r["my_hero"], r["enemy_hero"]): r for r in
            queries.personal_matchups(db, make_scope(account_id=1))}
    assert (7, 15) in rows
    assert rows[(7, 15)]["games"] == 3 and rows[(7, 15)]["wins"] == 3
