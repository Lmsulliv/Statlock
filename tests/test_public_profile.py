"""GET /api/players/{id}/profile — the public, label-free profile header shared
with the /player/{id} route and the link-preview og tags.

Key contracts:
- names resolve WITHOUT any viewer's private label (persona > bare id);
- current rank is the last mmr-history point, tolerating an empty ranks table;
- an unknown account returns 200 with has_data=False (never 404);
- the endpoint is side-effect-free (no discovery-request mailbox write).
"""
from fastapi.testclient import TestClient

from api.app import app
from tests.conftest import ENEMY, ME

UNTRACKED = 777_777          # an id that appears nowhere in the seeded world


def _client():
    return TestClient(app)


def test_profile_falls_back_to_bare_id(api_db):
    # No persona and no label for ME -> the display name is the bare account id.
    body = _client().get(f"/api/players/{ME}/profile").json()
    assert body["account_id"] == ME
    assert body["display_name"] == str(ME)
    assert body["has_data"] is True


def test_profile_prefers_persona_over_bare_id(api_db):
    api_db.execute(
        "INSERT INTO steam_personas(account_id, persona_name, fetched_at)"
        " VALUES (?, 'CoolName', '2026-06-15T12:00:00+00:00')",
        (ME,),
    )
    api_db.commit()
    body = _client().get(f"/api/players/{ME}/profile").json()
    assert body["display_name"] == "CoolName"


def test_profile_never_leaks_private_labels(api_db):
    # A manual label the default user (user 1) set must not surface on the public
    # profile: it resolves with user_id=None, which skips labels entirely.
    api_db.execute(
        "INSERT INTO account_labels(user_id, account_id, display_name)"
        " VALUES (1, ?, 'SecretNickname')",
        (ME,),
    )
    api_db.commit()
    body = _client().get(f"/api/players/{ME}/profile").json()
    assert body["display_name"] != "SecretNickname"
    assert body["display_name"] == str(ME)


def test_profile_reports_current_rank(api_db):
    # Seed a two-point rank history + the ranks table; the current rank is the
    # last (latest) point resolved against its tier.
    api_db.execute("INSERT INTO ranks(tier, name, color, fetched_at)"
                   " VALUES (5, 'Archon', '#abc', '2026-06-15T12:00:00+00:00')")
    for match_id, badge, ts in ((900, 40, "2026-06-01T00:00:00+00:00"),
                                (901, 52, "2026-06-10T00:00:00+00:00")):
        api_db.execute(
            "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
            " VALUES (?, ?, ?, ?)", (ME, match_id, badge, ts))
    api_db.commit()
    rank = _client().get(f"/api/players/{ME}/profile").json()["current_rank"]
    assert rank["badge"] == 52
    assert rank["tier"] == 5
    assert rank["subtier"] == 2
    assert rank["name"] == "Archon"


def test_profile_tolerates_empty_ranks_table(api_db):
    # Rank history exists but the ranks asset table is empty (never refreshed):
    # still 200, tier/subtier present, name None (not a crash).
    api_db.execute(
        "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
        " VALUES (?, 902, 33, '2026-06-10T00:00:00+00:00')", (ME,))
    api_db.commit()
    resp = _client().get(f"/api/players/{ME}/profile")
    assert resp.status_code == 200
    rank = resp.json()["current_rank"]
    assert rank["tier"] == 3 and rank["subtier"] == 3
    assert rank["name"] is None


def test_profile_has_data_for_co_player(api_db):
    # ENEMY isn't a tracked account, but it appears as a co-player in match rows,
    # so a shared link to it still renders a profile (has_data True).
    body = _client().get(f"/api/players/{ENEMY}/profile").json()
    assert body["has_data"] is True


def test_profile_unknown_account_is_200_without_data(api_db):
    resp = _client().get(f"/api/players/{UNTRACKED}/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is False
    assert body["display_name"] == str(UNTRACKED)
    assert body["current_rank"] is None


def test_profile_makes_no_discovery_request(api_db):
    before = api_db.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0]
    _client().get(f"/api/players/{ME}/profile")
    api_db.commit()
    after = api_db.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0]
    assert after == before
