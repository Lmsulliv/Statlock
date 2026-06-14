"""Reference-table loaders: heroes, items, patch_eras.

All functions take already-parsed JSON, not URLs, so they are fully testable
without network access. The live-fetch wiring lives in refresh_assets.py.
"""
import sqlite3
from typing import Any


def load_heroes(conn: sqlite3.Connection, heroes_json: list[dict], fetched_at: str) -> None:
    """Upsert heroes from the /v1/assets/heroes response into the heroes table.

    image_url comes from images.icon_hero_card (may be absent for unreleased heroes).
    Running again with the same data is a no-op (idempotent upsert).
    """
    rows = [
        (
            hero["id"],
            hero["name"],
            hero.get("images", {}).get("icon_hero_card"),
            fetched_at,
        )
        for hero in heroes_json
    ]
    conn.executemany(
        """INSERT INTO heroes(hero_id, name, image_url, fetched_at)
           VALUES(?, ?, ?, ?)
           ON CONFLICT(hero_id) DO UPDATE SET
               name       = excluded.name,
               image_url  = excluded.image_url,
               fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def load_items(conn: sqlite3.Connection, items_json: list[dict], fetched_at: str) -> None:
    """Upsert shop items (type=='upgrade') from /v1/assets/items into the items table.

    Prefer shop_image over image for image_url; both may be absent.
    Non-upgrade entries (abilities, weapon entries) are silently skipped.
    """
    rows = [
        (
            item["id"],
            item["name"],
            item.get("item_tier"),
            item.get("item_slot_type"),
            item.get("shop_image") or item.get("image"),
            fetched_at,
        )
        for item in items_json
        if item.get("type") == "upgrade"
    ]
    conn.executemany(
        """INSERT INTO items(item_id, name, tier, slot_type, image_url, fetched_at)
           VALUES(?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_id) DO UPDATE SET
               name       = excluded.name,
               tier       = excluded.tier,
               slot_type  = excluded.slot_type,
               image_url  = excluded.image_url,
               fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def load_ranks(conn: sqlite3.Connection, ranks_json: list[dict], fetched_at: str) -> None:
    """Upsert rank tiers from the /v1/assets/ranks response into the ranks table.

    Stores the semantic bits (tier, name, accent color). The badge art URL is a
    pure function of the tier and is derived in the read layer, not stored here.
    Idempotent upsert: re-running with the same data is a no-op.
    """
    rows = [
        (rank["tier"], rank["name"], rank.get("color"), fetched_at)
        for rank in ranks_json
    ]
    conn.executemany(
        """INSERT INTO ranks(tier, name, color, fetched_at)
           VALUES(?, ?, ?, ?)
           ON CONFLICT(tier) DO UPDATE SET
               name       = excluded.name,
               color      = excluded.color,
               fetched_at = excluded.fetched_at""",
        rows,
    )
    conn.commit()


def seed_patch_eras(conn: sqlite3.Connection, eras_json: list[dict]) -> None:
    """Insert patch eras from a seed list if they do not already exist.

    Keyed on started_at (which has a UNIQUE constraint), so re-running the
    seed never duplicates rows.
    """
    rows = [(era["label"], era["started_at"]) for era in eras_json]
    conn.executemany(
        "INSERT OR IGNORE INTO patch_eras(label, started_at) VALUES(?, ?)",
        rows,
    )
    conn.commit()
