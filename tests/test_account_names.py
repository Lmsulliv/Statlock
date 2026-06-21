"""Resolver + rename-API tests for manual account labels (Phase 2).

resolve_names precedence is: manual label (this owner, then the global-0 owner) >
Steam persona > bare account id. The rename API (PUT/DELETE /api/accounts/{id}/name)
is the single owner-gated namer and writes account_labels -- for ANY account, not
just tracked ones, since co-players/opponents are the point.
"""
from fastapi.testclient import TestClient

from api.app import app
from api.queries import GLOBAL_OWNER, resolve_names

REF = "2026-06-20T00:00:00+00:00"


def _label(conn, account_id, name, *, owner_id=GLOBAL_OWNER):
    conn.execute(
        "INSERT INTO account_labels(owner_id, account_id, display_name, updated_at)"
        " VALUES (?, ?, ?, ?)", (owner_id, account_id, name, REF))


def _persona(conn, account_id, name):
    conn.execute(
        "INSERT INTO steam_personas(account_id, persona_name, avatar_url, fetched_at)"
        " VALUES (?, ?, NULL, ?)", (account_id, name, REF))


def _client():
    return TestClient(app)


def _label_row(conn, account_id, owner_id=GLOBAL_OWNER):
    return conn.execute(
        "SELECT display_name FROM account_labels WHERE owner_id=? AND account_id=?",
        (owner_id, account_id)).fetchone()


# ── resolve_names precedence ─────────────────────────────────────────────────
def test_label_beats_persona_beats_id(db):
    _label(db, 10, "LabelTen")
    _persona(db, 10, "PersonaTen")
    _persona(db, 20, "PersonaTwenty")
    db.commit()
    assert resolve_names(db, [10, 20, 30]) == {10: "LabelTen", 20: "PersonaTwenty", 30: "30"}


def test_null_persona_falls_through_to_id(db):
    _persona(db, 40, None)   # private/unresolved placeholder row (persona_name NULL)
    db.commit()
    assert resolve_names(db, [40]) == {40: "40"}


def test_owner_label_beats_global_label(db):
    _label(db, 50, "GlobalFifty", owner_id=GLOBAL_OWNER)
    _label(db, 50, "FiveFifty", owner_id=5)
    db.commit()
    assert resolve_names(db, [50], owner_id=5)[50] == "FiveFifty"
    assert resolve_names(db, [50])[50] == "GlobalFifty"


def test_owner_falls_back_to_global_label(db):
    _label(db, 60, "GlobalSixty", owner_id=GLOBAL_OWNER)
    db.commit()
    # owner 5 has no label of its own -> falls back to the global-0 label.
    assert resolve_names(db, [60], owner_id=5)[60] == "GlobalSixty"


def test_resolve_dedupes_and_covers_every_id(db):
    _label(db, 10, "Ten")
    db.commit()
    assert resolve_names(db, [10, 10, 99]) == {10: "Ten", 99: "99"}


# ── rename API: owner gate ───────────────────────────────────────────────────
def test_put_name_forbidden_without_owner(api_db):
    assert _client().put("/api/accounts/1/name", json={"display_name": "x"}).status_code == 403


def test_delete_name_forbidden_without_owner(api_db):
    assert _client().delete("/api/accounts/1/name").status_code == 403


# ── rename API: upsert + revert ──────────────────────────────────────────────
def test_put_sets_label_and_resolves(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().put("/api/accounts/1/name", json={"display_name": "Main"})
    assert resp.status_code == 200
    assert resp.json() == {"account_id": 1, "display_name": "Main"}
    assert _label_row(api_db, 1)["display_name"] == "Main"


def test_put_upserts_existing_label(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    _client().put("/api/accounts/1/name", json={"display_name": "First"})
    resp = _client().put("/api/accounts/1/name", json={"display_name": "Second"})
    assert resp.json()["display_name"] == "Second"
    count = api_db.execute(
        "SELECT COUNT(*) FROM account_labels WHERE owner_id=0 AND account_id=1"
    ).fetchone()[0]
    assert count == 1   # upsert, not a duplicate row


def test_delete_reverts_to_persona_then_id(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    _client().put("/api/accounts/1/name", json={"display_name": "Main"})
    api_db.execute("INSERT INTO steam_personas(account_id, persona_name, avatar_url,"
                   " fetched_at) VALUES (1, 'SteamName', NULL, ?)", (REF,))
    api_db.commit()
    resp = _client().delete("/api/accounts/1/name")
    assert resp.status_code == 200
    assert resp.json() == {"account_id": 1, "display_name": "SteamName"}  # reverted to persona
    assert _label_row(api_db, 1) is None


def test_delete_is_idempotent(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().delete("/api/accounts/999/name")   # never had a label
    assert resp.status_code == 200
    assert resp.json() == {"account_id": 999, "display_name": "999"}


def test_put_works_for_untracked_account(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    # 900000 is a co-player/opponent account that is NOT tracked (the whole point).
    resp = _client().put("/api/accounts/900000/name", json={"display_name": "Rival"})
    assert resp.status_code == 200
    assert _label_row(api_db, 900000)["display_name"] == "Rival"


def test_put_empty_name_is_400(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    assert _client().put("/api/accounts/1/name",
                         json={"display_name": "   "}).status_code == 400


# ── accounts list resolves ───────────────────────────────────────────────────
def test_accounts_list_uses_resolver(api_db, monkeypatch):
    # The seeded self account (1) has no manual name; give it a persona and assert
    # the switcher shows the resolved persona, not None or the bare id.
    api_db.execute("INSERT INTO steam_personas(account_id, persona_name, avatar_url,"
                   " fetched_at) VALUES (1, 'SteamSelf', NULL, ?)", (REF,))
    api_db.commit()
    body = _client().get("/api/accounts").json()
    me = next(a for a in body if a["account_id"] == 1)
    assert me["display_name"] == "SteamSelf"
    assert me["is_self"] is True
