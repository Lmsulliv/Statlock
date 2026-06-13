"""Unit tests for ingest.parse: metadata JSON -> database rows."""
import json

import pytest

from ingest.parse import (
    era_id_for,
    finals_from_stats,
    insert_match,
    parse_metadata,
    unix_to_iso,
)
from tracker.reference import load_heroes, load_items

from tests.fakes import load_fixture

ME = 891231519
MATCH_ID = 86714494


@pytest.fixture
def meta():
    return load_fixture(f"match_metadata_{MATCH_ID}.json")


@pytest.fixture
def shop_ids():
    return {item["id"] for item in load_fixture("assets_items_match.json")}


def parse(meta, shop_ids, era_id=None):
    return parse_metadata(meta, json.dumps(meta), shop_ids, era_id, "2026-06-11T12:00:00+00:00")


def test_match_row_fields(meta, shop_ids):
    parsed = parse(meta, shop_ids, era_id=1)
    m = parsed.match_row
    mi = meta["match_info"]
    assert m["match_id"] == MATCH_ID
    assert m["duration_s"] == mi["duration_s"]
    assert m["winning_team"] == mi["winning_team"]
    assert m["average_badge_team0"] == mi["average_badge_team0"]
    assert m["average_badge_team1"] == mi["average_badge_team1"]
    assert m["era_id"] == 1
    assert m["start_time"] == unix_to_iso(mi["start_time"])
    assert json.loads(m["raw_json"]) == meta  # full payload archived untouched


def test_twelve_players_with_won_derived(meta, shop_ids):
    parsed = parse(meta, shop_ids)
    assert len(parsed.players) == 12
    winning = meta["match_info"]["winning_team"]
    for p, raw in zip(parsed.players, meta["match_info"]["players"]):
        assert p["account_id"] == raw["account_id"]
        assert p["hero_id"] == raw["hero_id"]
        assert p["lane"] == raw["assigned_lane"]
        assert p["won"] == int(raw["team"] == winning)


def test_finals_come_from_last_stats_entry(meta, shop_ids):
    parsed = parse(meta, shop_ids)
    me_raw = next(p for p in meta["match_info"]["players"] if p["account_id"] == ME)
    me = next(p for p in parsed.players if p["account_id"] == ME)
    last = me_raw["stats"][-1]
    assert me["player_damage"] == last["player_damage"]
    assert me["obj_damage"] == last["boss_damage"]
    assert me["healing"] == last["player_healing"]


def test_missing_or_empty_stats_yield_nulls_never_zeros(meta, shop_ids):
    # A zero is a claim; a NULL is an admission. Empty series must not
    # pollute averages with fake zeros.
    meta["match_info"]["players"][0]["stats"] = []
    del meta["match_info"]["players"][1]["stats"]
    parsed = parse(meta, shop_ids)
    for player in parsed.players[:2]:
        assert player["player_damage"] is None
        assert player["obj_damage"] is None
        assert player["healing"] is None


def test_finals_from_stats_helper():
    assert finals_from_stats([]) == (None, None, None)
    assert finals_from_stats(None) == (None, None, None)
    series = [
        {"player_damage": 10, "boss_damage": 1, "player_healing": 0},
        {"player_damage": 999, "boss_damage": 55, "player_healing": 42},
    ]
    assert finals_from_stats(series) == (999, 55, 42)


def test_purchases_filtered_to_shop_items(meta, shop_ids):
    parsed = parse(meta, shop_ids)
    assert parsed.purchases, "expected at least one shop purchase"
    for match_id, account_id, item_id, purchase_time_s, sold_time_s in parsed.purchases:
        assert match_id == MATCH_ID
        assert item_id in shop_ids  # ability/level-up entries filtered out
    raw_entries = sum(len(p["items"]) for p in meta["match_info"]["players"])
    assert len(parsed.purchases) < raw_entries


def test_insert_match_writes_all_rows(db, meta, shop_ids):
    load_heroes(db, load_fixture("assets_heroes_match.json"), "t")
    load_items(db, load_fixture("assets_items_match.json"), "t")
    parsed = parse(meta, shop_ids)
    with db:
        insert_match(db, parsed)
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM match_players").fetchone()[0] == 12
    n_purchases = db.execute("SELECT COUNT(*) FROM match_item_purchases").fetchone()[0]
    assert n_purchases == len(parsed.purchases)


def test_unix_to_iso():
    assert unix_to_iso(0) == "1970-01-01T00:00:00+00:00"


def test_era_id_for_picks_latest_era_started_before_match(db):
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('old', '2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('new', '2026-06-01T00:00:00+00:00')")
    db.commit()
    old_id = db.execute("SELECT era_id FROM patch_eras WHERE label='old'").fetchone()[0]
    new_id = db.execute("SELECT era_id FROM patch_eras WHERE label='new'").fetchone()[0]
    assert era_id_for(db, "2026-03-15T10:00:00+00:00") == old_id
    assert era_id_for(db, "2026-06-02T00:00:00+00:00") == new_id
    assert era_id_for(db, "2025-12-31T23:59:59+00:00") is None
