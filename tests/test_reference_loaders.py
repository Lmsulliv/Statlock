"""Tests: reference-table loaders (heroes, items, patch_eras seed)."""
import json
from pathlib import Path

import pytest

from tracker.reference import load_heroes, load_items, seed_patch_eras

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = "2026-06-11T00:00:00Z"


@pytest.fixture
def heroes_json():
    return json.loads((FIXTURES / "assets_heroes.json").read_text(encoding="utf-8"))


@pytest.fixture
def items_json():
    return json.loads((FIXTURES / "assets_items.json").read_text(encoding="utf-8"))


# ── heroes ───────────────────────────────────────────────────────────────────

def test_load_heroes_inserts_all_rows(db, heroes_json):
    load_heroes(db, heroes_json, FETCHED_AT)
    count = db.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    assert count == len(heroes_json)


def test_load_heroes_correct_name(db, heroes_json):
    load_heroes(db, heroes_json, FETCHED_AT)
    row = db.execute("SELECT name FROM heroes WHERE hero_id = 1").fetchone()
    assert row is not None
    assert row["name"] == "Infernus"


def test_load_heroes_image_url_populated(db, heroes_json):
    load_heroes(db, heroes_json, FETCHED_AT)
    # At least the heroes that have icon_hero_card should have a non-null image_url
    rows = db.execute(
        "SELECT image_url FROM heroes WHERE image_url IS NOT NULL"
    ).fetchall()
    assert len(rows) >= 4  # fixture has 5 heroes, >=4 should have images


def test_load_heroes_idempotent(db, heroes_json):
    load_heroes(db, heroes_json, FETCHED_AT)
    load_heroes(db, heroes_json, FETCHED_AT)
    count = db.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    assert count == len(heroes_json)


# ── items ────────────────────────────────────────────────────────────────────

def test_load_items_inserts_all_rows(db, items_json):
    load_items(db, items_json, FETCHED_AT)
    count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == len(items_json)


def test_load_items_slot_types(db, items_json):
    load_items(db, items_json, FETCHED_AT)
    slots = {
        row[0]
        for row in db.execute("SELECT DISTINCT slot_type FROM items").fetchall()
    }
    assert slots == {"weapon", "vitality", "spirit"}


def test_load_items_tier_populated(db, items_json):
    load_items(db, items_json, FETCHED_AT)
    nulls = db.execute("SELECT COUNT(*) FROM items WHERE tier IS NULL").fetchone()[0]
    assert nulls == 0


def test_load_items_idempotent(db, items_json):
    load_items(db, items_json, FETCHED_AT)
    load_items(db, items_json, FETCHED_AT)
    count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == len(items_json)


# ── patch_eras seed ──────────────────────────────────────────────────────────

# The real seed that drives fresh installs (and that migration 013 mirrors).
_SEED_PATH = Path(__file__).parent.parent / "db" / "seeds" / "patch_eras.json"
_SEED = json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _fresh_eras(db):
    """The db fixture is already migrated (migration 013 seeds the same 12 eras),
    so clear them first to test seed_patch_eras' own insert behavior."""
    db.execute("DELETE FROM patch_eras")
    db.commit()


def test_seed_patch_eras_inserts_curated_set(db):
    _fresh_eras(db)
    seed_patch_eras(db, _SEED)
    count = db.execute("SELECT COUNT(*) FROM patch_eras").fetchone()[0]
    assert count == len(_SEED) == 12


def test_seed_patch_eras_first_and_last_in_order(db):
    _fresh_eras(db)
    seed_patch_eras(db, _SEED)
    rows = db.execute(
        "SELECT label, started_at FROM patch_eras ORDER BY started_at"
    ).fetchall()
    assert rows[0]["label"] == "Major Map Rework"
    assert rows[0]["started_at"] == "2025-02-25T00:00:00Z"
    assert rows[-1]["label"] == "Minor Update (Jun 11)"
    assert rows[-1]["started_at"] == "2026-06-11T00:00:00Z"


def test_seed_patch_eras_idempotent(db):
    _fresh_eras(db)
    seed_patch_eras(db, _SEED)
    seed_patch_eras(db, _SEED)
    count = db.execute("SELECT COUNT(*) FROM patch_eras").fetchone()[0]
    assert count == len(_SEED)
