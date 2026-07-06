"""Regression: account_labels are private per user and never leak across viewers.

User A labels an opponent account; A sees the label, but a different logged-in
user (B) and an anonymous request see the Steam persona / bare id instead. Covers
both name-resolving read paths through the API: recurring-players and match detail.
"""
import json

import api.auth
from fastapi.testclient import TestClient

from api.app import app

BASE = "http://stats.example.com"      # http:// so login cookies aren't Secure-only
ENEMY = 900_000                        # the opponent account in every api_db match
LABEL = "SmurfAlt"
A_STEAM, B_STEAM = 555, 556            # distinct Steam ids -> distinct users


def _login(client, monkeypatch, steam_account_id):
    monkeypatch.setattr(api.auth, "verify_callback",
                        lambda params, **kw: steam_account_id)
    resp = client.get("/api/auth/callback?openid.claimed_id=x", follow_redirects=False)
    assert resp.status_code == 303


def _label_enemy(client):
    resp = client.put(f"/api/accounts/{ENEMY}/name", json={"display_name": LABEL},
                      headers={"X-CSRF-Token": client.cookies["csrf"]})
    assert resp.status_code == 200


# ── recurring-players: co-player names resolve for the requesting viewer ───────
def _opponent_name(body, account_id):
    for row in body["opponents"] + body["teammates"]:
        if row["account_id"] == account_id:
            return row["display_name"]
    return None


def test_recurring_players_label_is_private_to_its_user(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)   # auth on

    a = TestClient(app)
    _login(a, monkeypatch, A_STEAM)
    _label_enemy(a)
    # A sees their own label on the shared opponent.
    a_body = a.get("/api/recurring-players?account_id=1").json()
    assert _opponent_name(a_body, ENEMY) == LABEL

    # A different logged-in user (B) sees the bare id, never A's private label.
    b = TestClient(app)
    _login(b, monkeypatch, B_STEAM)
    b_body = b.get("/api/recurring-players?account_id=1").json()
    assert _opponent_name(b_body, ENEMY) == str(ENEMY)

    # An anonymous viewer (no session) sees the bare id too.
    anon_body = TestClient(app).get("/api/recurring-players?account_id=1").json()
    assert _opponent_name(anon_body, ENEMY) == str(ENEMY)


# ── match detail: roster names resolve for the requesting viewer ──────────────
MATCH_ID = 99_001


def _seed_match_with_roster(conn) -> None:
    # api_db's matches store raw_json '{}', so the roster parse yields nothing.
    # Seed one match whose raw_json carries a real roster including ENEMY.
    raw = {"match_info": {"winning_team": 0, "players": [
        {"player_slot": 1, "account_id": 1, "hero_id": 7, "team": 0},
        {"player_slot": 2, "account_id": ENEMY, "hero_id": 17, "team": 1},
    ]}}
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, '2026-06-15T12:00:00+00:00', 1800, '1',"
        " 0, 2, 50, 50, ?, '2026-06-15T12:00:00+00:00')",
        (MATCH_ID, json.dumps(raw)),
    )
    conn.commit()


def _roster_name(body, account_id):
    for p in body["players"]:
        if p["account_id"] == account_id:
            return p["display_name"]
    return None


def test_match_detail_label_is_private_to_its_user(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    _seed_match_with_roster(api_db)

    a = TestClient(app)
    _login(a, monkeypatch, A_STEAM)
    _label_enemy(a)
    a_body = a.get(f"/api/matches/{MATCH_ID}").json()
    assert _roster_name(a_body, ENEMY) == LABEL

    b = TestClient(app)
    _login(b, monkeypatch, B_STEAM)
    b_body = b.get(f"/api/matches/{MATCH_ID}").json()
    assert _roster_name(b_body, ENEMY) == str(ENEMY)

    anon_body = TestClient(app).get(f"/api/matches/{MATCH_ID}").json()
    assert _roster_name(anon_body, ENEMY) == str(ENEMY)
