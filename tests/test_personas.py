"""Unit/acceptance tests for ingest.personas: Steam persona resolution.

Test-first (CLAUDE.md): all HTTP goes through FakeClient; no test touches the
network. Persona tests opt into a key with monkeypatch.setenv -- the autouse
_no_steam_key fixture clears it by default so the dev's real key never leaks in.
"""
import json
import urllib.parse

from ingest.accounts import STEAMID64_OFFSET, to_account_id, to_steamid64
from ingest.personas import MAX_PER_CYCLE, refresh_personas

from tests.fakes import FakeClient, ManualNow, fixture_text

REF = "2026-01-01T00:00:00+00:00"


def seed_match_players(conn, account_ids):
    """Insert match_players rows for the given account_ids (FKs are ON, so a hero
    and matches must exist). Up to 12 players per match, rolling over as needed."""
    conn.execute("INSERT OR IGNORE INTO heroes(hero_id, name, fetched_at)"
                 " VALUES (1, 'Wraith', ?)", (REF,))
    for i, account_id in enumerate(account_ids):
        match_id = i // 12 + 1
        slot = i % 12 + 1
        conn.execute(
            "INSERT OR IGNORE INTO matches(match_id, start_time, duration_s,"
            " winning_team, raw_json, ingested_at) VALUES (?, ?, 1800, 0, '{}', ?)",
            (match_id, REF, REF),
        )
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
            " team, won) VALUES (?, ?, ?, 1, 0, 0)",
            (match_id, slot, account_id),
        )
    conn.commit()


def summaries(name_by_account):
    """A canned 200 GetPlayerSummaries response for {account_id: persona_name}."""
    players = [
        {"steamid": str(to_steamid64(acct)), "personaname": name,
         "avatarfull": f"https://av/{acct}_full.jpg"}
        for acct, name in name_by_account.items()
    ]
    return (200, {}, json.dumps({"response": {"players": players}}))


def steamids_of(url):
    qs = urllib.parse.urlparse(url).query
    return {int(s) for s in urllib.parse.parse_qs(qs)["steamids"][0].split(",")}


# ── 1. conversion is exact and reversible ────────────────────────────────────
def test_steamid_conversion_is_exact_and_reversible():
    assert to_steamid64(100) == 100 + STEAMID64_OFFSET
    for acct in (1, 100, 12_345, 999_999):
        assert to_account_id(to_steamid64(acct)) == acct


# ── 2. batch builds the right steamid list; skips account_id <= 0 ────────────
def test_batch_skips_nonpositive_account_ids(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, [100, 200, 0, -5, 300])
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({100: "A", 200: "B", 300: "C"}))

    refresh_personas(db, client, now=ManualNow())

    assert len(client.calls) == 1
    assert steamids_of(client.calls[0]) == {to_steamid64(a) for a in (100, 200, 300)}


# ── 3. personaname and avatar map to the table (real-shape fixture) ──────────
def test_persona_name_and_avatar_stored(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, [100, 300])
    client = FakeClient()
    client.add("GetPlayerSummaries", (200, {}, fixture_text("steam_player_summaries.json")))

    refresh_personas(db, client, now=ManualNow())

    rows = {r["account_id"]: r for r in db.execute("SELECT * FROM steam_personas")}
    assert rows[100]["persona_name"] == "Alice"
    # avatarfull (184px) is stored, not the smaller avatar/avatarmedium variants.
    assert rows[100]["avatar_url"] == "https://avatars.steamstatic.com/alice_full.jpg"
    assert rows[300]["persona_name"] == "Carol"


# ── 4. private/missing result handled; gets a NULL placeholder, no crash ─────
def test_missing_profile_gets_null_placeholder(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, [100, 200, 300])
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({100: "A", 300: "C"}))  # 200 omitted

    refresh_personas(db, client, now=ManualNow())

    rows = {r["account_id"]: r for r in db.execute("SELECT * FROM steam_personas")}
    assert set(rows) == {100, 200, 300}
    assert rows[200]["persona_name"] is None
    assert rows[200]["avatar_url"] is None
    assert rows[200]["fetched_at"] is not None  # placeholder dated so it ages out


# ── 5. stale rows refresh; fresh rows are not re-fetched ─────────────────────
def test_stale_refreshes_fresh_skipped(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    now = ManualNow()
    seed_match_players(db, [100, 200])
    db.execute("INSERT INTO steam_personas(account_id, persona_name, avatar_url,"
               " fetched_at) VALUES (100, 'OldName', NULL, ?)", (REF,))  # stale
    db.execute("INSERT INTO steam_personas(account_id, persona_name, avatar_url,"
               " fetched_at) VALUES (200, 'FreshName', NULL, ?)", (now().isoformat(),))
    db.commit()
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({100: "NewName"}))

    refresh_personas(db, client, now=now)

    assert steamids_of(client.calls[0]) == {to_steamid64(100)}  # only the stale one
    names = {r["account_id"]: r["persona_name"] for r in db.execute("SELECT * FROM steam_personas")}
    assert names[100] == "NewName"
    assert names[200] == "FreshName"


# ── 6. no key -> clean no-op ─────────────────────────────────────────────────
def test_no_key_is_noop(db, monkeypatch):
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    seed_match_players(db, [100, 200])
    client = FakeClient()

    assert refresh_personas(db, client, now=ManualNow()) == 0
    assert client.calls == []
    assert db.execute("SELECT COUNT(*) FROM steam_personas").fetchone()[0] == 0


# ── 7. raw response archived AND the API key is redacted from the URL ────────
def test_response_archived_with_key_redacted(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, [100])
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({100: "A"}))

    refresh_personas(db, client, now=ManualNow())

    rows = db.execute(
        "SELECT url FROM raw_api_responses WHERE url LIKE '%GetPlayerSummaries%'"
    ).fetchall()
    assert len(rows) == 1
    assert "TESTKEY" not in rows[0]["url"]
    assert "key=***" in rows[0]["url"]


# ── 8. batching of 100, and the per-cycle cap ────────────────────────────────
def test_batches_in_chunks_of_100(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, list(range(1, 151)))   # 150 due -> 100 + 50
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({}))

    refresh_personas(db, client, now=ManualNow())

    assert len(client.calls) == 2


def test_cap_limits_accounts_per_cycle(db, monkeypatch):
    monkeypatch.setenv("STEAM_API_KEY", "TESTKEY")
    seed_match_players(db, list(range(1, 601)))   # 600 due, cap at MAX_PER_CYCLE
    client = FakeClient()
    client.add("GetPlayerSummaries", summaries({}))

    refresh_personas(db, client, now=ManualNow())

    assert len(client.calls) == MAX_PER_CYCLE // 100   # == 5
