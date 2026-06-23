"""The six presentation-spec acceptance scenarios, plus game-mode separation
and an API==CLI regression test.

These run against the seeded `api_db` fixture (tests/conftest.py); the FastAPI
app and the CLI both read it via the DEADLOCK_DB env var the fixture sets.
"""
from fastapi.testclient import TestClient

from api import queries, service
from api.app import app
from api.scope import make_scope
from ingest.maintenance import DECADE_BRACKETS
from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_LEANING_STRENGTH,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
)
from stats import __main__ as cli
from tests.conftest import (
    E_BRACKET,
    E_BRAWL,
    E_ERA,
    E_TWO,
    E_WEAK,
    E_WATCH,
    ERA2,
    HERO_ME,
    ME,
    SNAPSHOT,
)


# ── Scenario 1: a 2-game matchup never displays a verdict ─────────────────────

def test_two_game_matchup_never_shows_verdict(api_db):
    # min_games=1 so the row is shown at all; the verdict must still be neutral
    # because the Wilson interval at n=2 is far too wide to exclude the global.
    rows = service.matchups(api_db, make_scope(min_games=1))
    haze = next(r for r in rows if r["enemy_hero_id"] == E_TWO)
    assert haze["games"] == 2 and haze["wins"] == 2     # a perfect record...
    assert haze["verdict"] == VERDICT_NOT_ENOUGH_DATA   # ...still no verdict


# ── Scenario 2: the rank slider moves personal AND baseline consistently ──────

def test_rank_slider_changes_personal_and_resums_baseline(api_db):
    full = service.matchups(api_db, make_scope(min_games=1, badge_min=0, badge_max=116))
    narrow = service.matchups(api_db, make_scope(min_games=1, badge_min=31, badge_max=60))
    f = next(r for r in full if r["enemy_hero_id"] == E_BRACKET)
    n = next(r for r in narrow if r["enemy_hero_id"] == E_BRACKET)

    assert f["games"] == 8 and n["games"] == 4          # personal stats change
    assert f["global_matches"] == 300                    # summed all 3 brackets
    assert n["global_matches"] == 100                    # only the 31-60 bracket


# ── Scenario 3: redrawing an era boundary recomputes, no re-ingestion ─────────

def test_era_redraw_recomputes_without_reingestion(api_db):
    before = service.matchups(api_db, make_scope(min_games=1, era_ids="2"))
    b = next(r for r in before if r["enemy_hero_id"] == E_ERA)
    assert b["games"] == 2          # only the two June matches start inside E2

    # Move E2's start back into May, then re-bin from start_time alone.
    api_db.execute("UPDATE patch_eras SET started_at = '2026-05-01T00:00:00+00:00'"
                   " WHERE era_id = ?", (ERA2,))
    api_db.commit()
    service.rebin_eras(api_db)

    after = service.matchups(api_db, make_scope(min_games=1, era_ids="2"))
    a = next(r for r in after if r["enemy_hero_id"] == E_ERA)
    assert a["games"] == 4          # now the May matches fall inside E2 too


# ── Scenario 4: a bookmarked scope renders identically ────────────────────────

def test_bookmarked_scope_is_deterministic(api_db):
    client = TestClient(app)
    params = {"min_games": 1, "badge_min": 31, "badge_max": 60, "era_ids": "2"}
    first = client.get("/api/matchups", params=params)
    second = client.get("/api/matchups", params=params)
    assert first.status_code == 200
    assert first.text == second.text      # byte-identical for the same query


# ── Scenario 5: improvement never shows an unconfirmed delta outside watch ─────

def test_improvement_watch_list_discipline(api_db):
    imp = service.improvement(api_db, make_scope(min_games=3))

    for entry in imp["confirmed_weaknesses"]:
        assert entry["verdict"] == VERDICT_CLEAR_WEAKNESS
    for entry in imp["confirmed_strengths"]:
        assert entry["verdict"] == VERDICT_CLEAR_STRENGTH
    for entry in imp["watch_list"]:
        assert entry["verdict"] in (VERDICT_LEANING_WEAKNESS, VERDICT_LEANING_STRENGTH)

    confirmed_enemies = {e.get("enemy_hero_id")
                         for e in imp["confirmed_weaknesses"] + imp["confirmed_strengths"]}
    watch_enemies = {e.get("enemy_hero_id") for e in imp["watch_list"]}
    assert E_WEAK in {e.get("enemy_hero_id") for e in imp["confirmed_weaknesses"]}
    assert E_WATCH in watch_enemies          # large delta, but unconfirmed
    assert E_WATCH not in confirmed_enemies   # ...so never in a confirmed list


# ── Scenario 6: an empty database renders helpful empty states, not errors ────

def test_empty_database_renders_empty_states(empty_db_path):
    client = TestClient(app)

    assert client.get("/api/matchups", params={"min_games": 1}).json() == []
    assert client.get("/api/items", params={"hero_id": 7}).json() == []

    improvement = client.get("/api/improvement")
    assert improvement.status_code == 200
    assert improvement.json()["watch_list"] == []

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["account_id"] is None

    assert client.get("/api/sync-status").status_code == 200
    assert client.get("/api/eras").json()["eras"] == []


# ── Game-mode separation: Street Brawl never mixes with Normal ────────────────

def test_game_mode_separation(api_db):
    normal = {r["enemy_hero_id"] for r in
              service.matchups(api_db, make_scope(min_games=1, game_mode="1"))}
    brawl = {r["enemy_hero_id"] for r in
             service.matchups(api_db, make_scope(min_games=1, game_mode="4"))}

    assert E_BRAWL not in normal      # the Brawl opponent is absent from Normal
    assert E_BRAWL in brawl           # ...and present under the Brawl scope
    assert E_WEAK in normal           # a Normal opponent...
    assert E_WEAK not in brawl        # ...is absent from Brawl


# ── Presentation pass: thin rows show in tables, the digest stays gated ───────

def test_thin_rows_now_appear_in_matchups_table(api_db):
    # min_games is no longer a row filter: the 2-game Haze row appears even at
    # min_games=5 (it used to be dropped), just without a verdict.
    rows = service.matchups(api_db, make_scope(min_games=5))
    haze = next(r for r in rows if r["enemy_hero_id"] == E_TWO)
    assert haze["games"] == 2
    assert haze["verdict"] == VERDICT_NOT_ENOUGH_DATA


def test_improvement_digest_stays_gated_by_min_games(api_db):
    # Lash is a confirmed weakness on 30 games. Raising min_games above 30 keeps
    # the row in the matchups TABLE but drops it from the improvement DIGEST.
    table_ids = {r["enemy_hero_id"]
                 for r in service.matchups(api_db, make_scope(min_games=31))}
    assert E_WEAK in table_ids                       # table shows the thin row

    imp = service.improvement(api_db, make_scope(min_games=31))
    digest_ids = {e.get("enemy_hero_id") for lst in imp.values() for e in lst}
    assert E_WEAK not in digest_ids                  # ...but the digest gates it

    # Sanity: at a low gate the same subject IS a confirmed weakness.
    imp_low = service.improvement(api_db, make_scope(min_games=3))
    assert E_WEAK in {e.get("enemy_hero_id") for e in imp_low["confirmed_weaknesses"]}


def test_item_rows_expose_image_url(api_db):
    rows = service.items(api_db, make_scope(min_games=1), HERO_ME)
    assert rows                                       # the hero has item rows
    assert all("item_image_url" in r for r in rows)


def test_recent_matches_expose_hero_image_url(api_db):
    overview = service.overview(api_db, make_scope())
    assert overview["last_matches"]
    assert all("image_url" in m for m in overview["last_matches"])


# ── Regression: the API and the CLI produce identical numbers ─────────────────

def test_api_and_cli_produce_identical_numbers(api_db, capsys):
    scope = make_scope(min_games=1)
    rows = service.matchups(api_db, scope)

    api_rows = TestClient(app).get("/api/matchups", params={"min_games": 1}).json()
    assert api_rows == rows                     # API returns exactly the service rows

    cli.main(["matchups", "--min-games", "1"])  # reads the same DB via DEADLOCK_DB
    out = capsys.readouterr().out
    assert out.strip() == cli.render_matchups(rows, scope, None).strip()


# ── Rank-bracket baselines: decade snapping + brackets-only re-sum ────────────

def test_make_scope_snaps_badge_range_to_decade_edges():
    """A mid-decade slider value snaps outward to its decade so the baseline
    containment predicate never splits a bracket; the (0,116) full-range
    sentinel is preserved (so the whole ladder still counts NULL-badge personal
    matches)."""
    mid = make_scope(badge_min=65, badge_max=65)
    assert (mid.badge_min, mid.badge_max) == (60, 69)

    span = make_scope(badge_min=35, badge_max=72)
    assert (span.badge_min, span.badge_max) == (30, 79)

    full = make_scope(badge_min=0, badge_max=116)
    assert (full.badge_min, full.badge_max) == (0, 116)
    assert full.is_full_badge_range


# Fresh ids so the seeded brackets don't collide with the shared _seed rows.
_RS_HERO, _RS_ENEMY, _RS_ITEM = 777, 778, 779


def _seed_decade_brackets(conn):
    """Seed one matchup + one item baseline row per decade bracket (era 0), with
    wins varying by bracket so a re-sum can't be faked by reading a single row.
    Returns (total_wins, total_matches) across the 12 brackets."""
    total_wins = total_matches = 0
    for i, (bmin, bmax) in enumerate(DECADE_BRACKETS):
        wins, matches = i + 1, 10
        total_wins += wins
        total_matches += matches
        conn.execute(
            "INSERT INTO baseline_hero_matchups(snapshot_id, hero_id, enemy_hero_id,"
            " era_id, badge_min, badge_max, wins, matches, fetched_at)"
            " VALUES (?, ?, ?, 0, ?, ?, ?, ?, '2026-06-15T12:00:00+00:00')",
            (SNAPSHOT, _RS_HERO, _RS_ENEMY, bmin, bmax, wins, matches),
        )
        conn.execute(
            "INSERT INTO baseline_hero_item_stats(snapshot_id, hero_id, item_id,"
            " era_id, badge_min, badge_max, wins, matches, avg_purchase_s, fetched_at)"
            " VALUES (?, ?, ?, 0, ?, ?, ?, ?, 300.0, '2026-06-15T12:00:00+00:00')",
            (SNAPSHOT, _RS_HERO, _RS_ITEM, bmin, bmax, wins, matches),
        )
    conn.commit()
    return total_wins, total_matches


def test_full_range_baseline_resums_all_decade_brackets(api_db):
    """A full-range scope re-sums all 12 decade brackets, reproducing the totals
    a single [0,116] row would have held (rated-only: every bracket counted)."""
    total_wins, total_matches = _seed_decade_brackets(api_db)
    full = make_scope()  # (0,116), contains every decade bracket

    matchups = queries.baseline_matchups(api_db, full, SNAPSHOT)
    assert matchups[(_RS_HERO, _RS_ENEMY)] == {"wins": total_wins, "matches": total_matches}

    items = queries.baseline_item_stats(api_db, full, _RS_HERO, SNAPSHOT)
    assert items[_RS_ITEM]["wins"] == total_wins
    assert items[_RS_ITEM]["matches"] == total_matches


def test_narrow_decade_scope_returns_only_its_bracket(api_db):
    """A snapped narrow scope returns nonempty baseline rows from exactly the one
    decade bracket it contains."""
    _seed_decade_brackets(api_db)
    narrow = make_scope(badge_min=60, badge_max=69)
    assert (narrow.badge_min, narrow.badge_max) == (60, 69)

    idx = DECADE_BRACKETS.index((60, 69))  # wins seeded as idx + 1, matches 10
    matchups = queries.baseline_matchups(api_db, narrow, SNAPSHOT)
    assert matchups[(_RS_HERO, _RS_ENEMY)] == {"wins": idx + 1, "matches": 10}

    items = queries.baseline_item_stats(api_db, narrow, _RS_HERO, SNAPSHOT)
    assert items[_RS_ITEM]["wins"] == idx + 1 and items[_RS_ITEM]["matches"] == 10


# ── Rank tiers from /api/ranks ───────────────────────────────────────────────

def test_api_ranks_returns_per_tier_entries(api_db):
    """/api/ranks returns one entry per rank tier (the badge filter only
    partitions cleanly at tier granularity -- api-findings finding 6), each with a
    derived per-tier badge URL, ordered low to high."""
    for tier, name in ((0, "Obscurus"), (1, "Initiate"), (8, "Oracle")):
        api_db.execute(
            "INSERT INTO ranks(tier, name, color, fetched_at)"
            " VALUES (?, ?, '#abcdef', '2026-06-15T12:00:00+00:00')",
            (tier, name),
        )
    api_db.commit()

    rows = TestClient(app).get("/api/ranks").json()

    assert [r["tier"] for r in rows] == [0, 1, 8]          # ordered low to high
    assert all({"tier", "name", "color", "badge_url"} == set(r) for r in rows)
    oracle = next(r for r in rows if r["tier"] == 8)
    assert oracle["name"] == "Oracle"
    assert oracle["badge_url"].endswith("rank8/badge_lg.png")


# ── Tracked accounts from /api/accounts ──────────────────────────────────────

def test_api_accounts_lists_tracked_accounts_self_first(api_db):
    """/api/accounts lists every tracked account for the viewer's switcher, the
    is_self account first, with is_self as a bool and a resolved display_name. The
    fixture seeds the self account (ME); add a second, labelled, non-self one."""
    other = 900_001
    api_db.execute(
        "INSERT INTO tracked_accounts(account_id, is_self, added_at)"
        " VALUES (?, 0, '2026-06-15T12:00:00+00:00')",
        (other,),
    )
    # The switcher lists the user's accounts via user_accounts (is_self lives there).
    api_db.execute(
        "INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
        " VALUES (1, ?, 0, '2026-06-15T12:00:00+00:00')",
        (other,),
    )
    # The manual name lives in account_labels now (the single source the resolver
    # reads); tracked_accounts.display_name is no longer consulted.
    api_db.execute(
        "INSERT INTO account_labels(user_id, account_id, display_name, updated_at)"
        " VALUES (1, ?, 'Smurf', '2026-06-15T12:00:00+00:00')",
        (other,),
    )
    api_db.commit()

    rows = TestClient(app).get("/api/accounts").json()

    assert [r["account_id"] for r in rows] == [ME, other]   # is_self first
    assert all({"account_id", "display_name", "is_self"} == set(r) for r in rows)
    me_row, other_row = rows
    assert me_row["is_self"] is True and other_row["is_self"] is False
    # No label/persona for the self account -> it resolves to its bare id.
    assert me_row["display_name"] == str(ME)
    assert other_row["display_name"] == "Smurf"   # the manual label
