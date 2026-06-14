"""Assembly layer: the shared "backend functions".

Takes a connection + a Scope, runs api.queries, applies the pure math from
stats/, and returns plain JSON-serializable dicts. Both api.app (FastAPI) and
stats/__main__.py (the CLI) call these, so the two can never disagree on a
number -- that's the whole point of the stepping-stone CLI.

Imports stats + tracker only; never FastAPI, so the CLI stays lightweight.
"""
import dataclasses
import sqlite3

from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_LEANING_STRENGTH,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
    shrunk_rate,
    verdict,
    wilson_interval,
)

from api import queries
from api.scope import Scope

_RATE_DP = 4   # decimal places for rates (keeps JSON clean and deterministic)
_TIME_DP = 1   # decimal places for purchase-timing seconds


def _round(x: float | None, ndigits: int = _RATE_DP) -> float | None:
    return None if x is None else round(x, ndigits)


def _resolved(conn: sqlite3.Connection, scope: Scope) -> Scope | None:
    """Scope with account_id filled from the is_self account. None if no account
    can be resolved (empty DB) -- callers return an empty state."""
    if scope.account_id is not None:
        return scope
    account_id = queries.resolve_self_account_id(conn)
    if account_id is None:
        return None
    return dataclasses.replace(scope, account_id=account_id)


def _stat_fields(wins: int, games: int, global_wins: int, global_matches: int) -> dict:
    """The shared statistics block: winrate + Wilson interval + shrinkage +
    verdict. Used identically by matchup and item rows."""
    low, high = wilson_interval(wins, games)
    winrate = wins / games if games else None
    fields = {
        "winrate": _round(winrate),
        "ci_low": _round(low),
        "ci_high": _round(high),
        "global_matches": global_matches,
    }
    if global_matches > 0:
        global_rate = global_wins / global_matches
        adjusted = shrunk_rate(wins, games, global_rate)
        fields.update({
            "global_rate": _round(global_rate),
            "adjusted_rate": _round(adjusted),
            # delta is the shrinkage-adjusted gap (spec's "Adjusted delta");
            # raw_delta is the plain personal-minus-global the user also wanted.
            "delta": _round(adjusted - global_rate),
            "raw_delta": _round(winrate - global_rate) if winrate is not None else None,
            "verdict": verdict(wins, games, global_rate),
        })
    else:
        # No baseline for this scope -> nothing to compare against.
        fields.update({
            "global_rate": None, "adjusted_rate": None, "delta": None,
            "raw_delta": None, "verdict": VERDICT_NOT_ENOUGH_DATA,
        })
    return fields


def _significant(row: dict) -> bool:
    """A confirmed call (used by the items sort and the improvement digest)."""
    return row["verdict"] in (VERDICT_CLEAR_STRENGTH, VERDICT_CLEAR_WEAKNESS)


# ── Matchups ─────────────────────────────────────────────────────────────────

def matchups(conn: sqlite3.Connection, scope: Scope,
             hero_id: int | None = None) -> list[dict]:
    """One row per enemy hero (optionally restricted to a hero you play).

    When hero_id is given, each row's baseline is the (hero, enemy) global rate.
    When it is omitted, personal counts are aggregated across the heroes you
    actually played, and the baseline is summed over exactly those same
    (your hero, enemy) pairs so personal and global stay comparable.
    """
    scope = _resolved(conn, scope)
    if scope is None:
        return []

    personal = queries.personal_matchups(conn, scope, my_hero_id=hero_id)
    snapshot = queries.latest_snapshot_id(conn)
    baseline = queries.baseline_matchups(conn, scope, snapshot) if snapshot else {}
    names = queries.hero_names(conn)
    images = queries.hero_images(conn)

    agg: dict[int, dict] = {}
    for row in personal:
        enemy = row["enemy_hero"]
        bucket = agg.setdefault(enemy, {"games": 0, "wins": 0, "gw": 0, "gm": 0})
        bucket["games"] += row["games"]
        bucket["wins"] += row["wins"] or 0
        b = baseline.get((row["my_hero"], enemy))
        if b:
            bucket["gw"] += b["wins"]
            bucket["gm"] += b["matches"]

    rows = []
    for enemy, b in agg.items():
        if b["games"] < scope.min_games:
            continue
        row = {
            "enemy_hero_id": enemy,
            "enemy_hero_name": names.get(enemy, str(enemy)),
            "enemy_hero_image_url": images.get(enemy),
            "games": b["games"],
            "wins": b["wins"],
        }
        row.update(_stat_fields(b["wins"], b["games"], b["gw"], b["gm"]))
        rows.append(row)

    # Default order is alphabetical by enemy hero; the UI re-sorts on click.
    rows.sort(key=lambda r: r["enemy_hero_name"].lower())
    return rows


# ── Items (per hero) ─────────────────────────────────────────────────────────

def items(conn: sqlite3.Connection, scope: Scope, hero_id: int) -> list[dict]:
    """One row per item for a chosen hero, plus a purchase-timing delta."""
    scope = _resolved(conn, scope)
    if scope is None:
        return []

    personal = queries.personal_item_stats(conn, scope, hero_id)
    snapshot = queries.latest_snapshot_id(conn)
    baseline = queries.baseline_item_stats(conn, scope, hero_id, snapshot) if snapshot else {}
    names = queries.item_names(conn)

    rows = []
    for p in personal:
        if p["games"] < scope.min_games:
            continue
        item_id = p["item_id"]
        b = baseline.get(item_id, {})
        gw, gm = b.get("wins", 0), b.get("matches", 0)
        personal_buy = p["avg_purchase_s"]
        global_buy = b.get("avg_purchase_s")
        timing_delta = (
            personal_buy - global_buy
            if personal_buy is not None and global_buy is not None else None
        )
        row = {
            "item_id": item_id,
            "item_name": names.get(item_id, str(item_id)),
            "games": p["games"],
            "wins": p["wins"],
            "avg_purchase_s": _round(personal_buy, _TIME_DP),
            "global_avg_purchase_s": _round(global_buy, _TIME_DP),
            "purchase_timing_delta_s": _round(timing_delta, _TIME_DP),
        }
        row.update(_stat_fields(p["wins"], p["games"], gw, gm))
        rows.append(row)

    rows.sort(key=lambda r: (0 if _significant(r) else 1,
                             -(abs(r["delta"]) if r["delta"] is not None else 0.0),
                             -r["games"], r["item_id"]))
    return rows


# ── Improvement digest ───────────────────────────────────────────────────────

def _played_hero_ids(conn: sqlite3.Connection, scope: Scope) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT hero_id FROM match_players mp"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ?",
        (scope.account_id, scope.game_mode),
    ).fetchall()
    return [r["hero_id"] for r in rows]


def _raw_delta(row: dict) -> float | None:
    if row["winrate"] is None or row["global_rate"] is None:
        return None
    return row["winrate"] - row["global_rate"]


def improvement(conn: sqlite3.Connection, scope: Scope) -> dict:
    """A short ranked digest across matchups and items: confirmed weaknesses,
    confirmed strengths, and a watch list of large-but-unconfirmed deltas.
    An unconfirmed delta never appears outside the watch list (scenario 5)."""
    empty = {"confirmed_weaknesses": [], "confirmed_strengths": [], "watch_list": []}
    resolved = _resolved(conn, scope)
    if resolved is None:
        return empty

    entries = [dict(r, kind="matchup", subject=r["enemy_hero_name"])
               for r in matchups(conn, resolved)]
    for hero_id in _played_hero_ids(conn, resolved):
        entries += [dict(r, kind="item", subject=r["item_name"], hero_id=hero_id)
                    for r in items(conn, resolved, hero_id)]

    weaknesses, strengths, watch = [], [], []
    for e in entries:
        v = e["verdict"]
        if v == VERDICT_CLEAR_WEAKNESS:
            weaknesses.append(e)
        elif v == VERDICT_CLEAR_STRENGTH:
            strengths.append(e)
        elif v in (VERDICT_LEANING_WEAKNESS, VERDICT_LEANING_STRENGTH):
            watch.append(e)        # a softer signal: real but not yet confirmed

    weaknesses.sort(key=lambda e: e["delta"])               # most negative first
    strengths.sort(key=lambda e: -e["delta"])               # most positive first
    watch.sort(key=lambda e: -abs(_raw_delta(e) or 0.0))    # biggest raw gap first
    return {"confirmed_weaknesses": weaknesses,
            "confirmed_strengths": strengths,
            "watch_list": watch}


# ── Heroes the account plays (the "my hero" picker) ──────────────────────────

def played_heroes(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Heroes the scoped account has played, each with name + icon URL, sorted
    by name. Powers the matchups hero-perspective selector."""
    resolved = _resolved(conn, scope)
    if resolved is None:
        return []
    names = queries.hero_names(conn)
    images = queries.hero_images(conn)
    heroes = [
        {"hero_id": hid, "name": names.get(hid, str(hid)), "image_url": images.get(hid)}
        for hid in _played_hero_ids(conn, resolved)
    ]
    heroes.sort(key=lambda h: h["name"])
    return heroes


# ── Rank tiers (for the rank-range selector) ─────────────────────────────────

_RANK_ART_BASE = "https://assets-bucket.deadlock-api.com/assets-api-res/images/ranks"


def _rank_badge_url(tier: int) -> str:
    """Derive the tier badge-art URL from the assets CDN layout. The URL is a
    pure function of the tier, so we derive it rather than storing it."""
    return f"{_RANK_ART_BASE}/rank{tier}/badge_lg.png"


def ranks(conn: sqlite3.Connection) -> list[dict]:
    """Rank tiers (name + color from the DB) plus a derived badge-art URL."""
    return [
        {
            "tier": r["tier"],
            "name": r["name"],
            "color": r["color"],
            "badge_url": _rank_badge_url(r["tier"]),
        }
        for r in queries.list_ranks(conn)
    ]


# ── Overview / sync / eras ───────────────────────────────────────────────────

def sync_status(conn: sqlite3.Connection) -> dict:
    counts = queries.queue_counts(conn)
    depth = counts.get("pending", 0) + counts.get("failed", 0)
    status = {
        "queue": counts,
        "queue_depth": depth,
        "fetched": counts.get("fetched", 0),
        "unavailable": counts.get("unavailable", 0),
        "last_discovery_at": queries.last_discovery_at(conn),
        "last_maintenance_at": queries.last_maintenance_at(conn),
        "pending_era_candidates": queries.pending_candidate_count(conn),
    }
    if not counts:
        status["message"] = "Nothing queued yet. Add an account and run the worker."
    return status


def overview(conn: sqlite3.Connection, scope: Scope) -> dict:
    sync = sync_status(conn)
    resolved = _resolved(conn, scope)
    if resolved is None:
        return {
            "account_id": None, "mmr_series": [], "last_matches": [], "sync": sync,
            "message": "No tracked account yet. Add one with:"
                       " python -m ingest add-account <id> --self",
        }

    names = queries.hero_names(conn)
    recent = queries.last_matches(conn, resolved.account_id)
    for m in recent:
        m["hero_name"] = names.get(m["hero_id"], str(m["hero_id"]))
        m["won"] = bool(m["won"])
    result = {
        "account_id": resolved.account_id,
        "mmr_series": queries.mmr_series(conn, resolved.account_id),
        "last_matches": recent,
        "sync": sync,
    }
    if not recent:
        result["message"] = "No matches ingested yet — the worker may still be syncing."
    return result


def eras(conn: sqlite3.Connection) -> dict:
    era_rows = queries.list_eras(conn)
    result = {"eras": era_rows, "pending_candidates": queries.list_pending_candidates(conn)}
    if not era_rows:
        result["message"] = "No eras defined yet."
    return result


# ── Writes (admin era management) ────────────────────────────────────────────

def rebin_eras(conn: sqlite3.Connection) -> int:
    """Recompute every match's era_id from its start_time against the current
    era boundaries. A single UPDATE, no re-ingestion: redrawing a boundary
    re-scopes every era-scoped stat (scenario 3). Returns rows updated."""
    cur = conn.execute(
        "UPDATE matches SET era_id = ("
        "  SELECT e.era_id FROM patch_eras e"
        "  WHERE e.started_at <= matches.start_time"
        "  ORDER BY e.started_at DESC LIMIT 1)"
    )
    conn.commit()
    return cur.rowcount


def confirm_candidate(conn: sqlite3.Connection, candidate_id: int) -> dict:
    """Confirm an era candidate: create the era boundary (which closes the prior
    era, since an era runs until the next one's start), mark it confirmed, and
    re-bin matches so the new era has correctly-scoped stats from day one."""
    cand = queries.get_candidate(conn, candidate_id)
    if cand is None:
        return {"ok": False, "error": "candidate not found"}

    label = cand["post_title"] or f"Era from candidate {candidate_id}"
    conn.execute(
        "INSERT OR IGNORE INTO patch_eras(label, started_at) VALUES (?, ?)",
        (label, cand["posted_at"]),
    )
    era_row = conn.execute(
        "SELECT era_id FROM patch_eras WHERE started_at = ?", (cand["posted_at"],)
    ).fetchone()
    conn.execute(
        "UPDATE era_candidates SET status = 'confirmed' WHERE candidate_id = ?",
        (candidate_id,),
    )
    conn.commit()
    rebinned = rebin_eras(conn)
    return {"ok": True, "era_id": era_row["era_id"] if era_row else None,
            "rebinned": rebinned}


def dismiss_candidate(conn: sqlite3.Connection, candidate_id: int) -> dict:
    cand = queries.get_candidate(conn, candidate_id)
    if cand is None:
        return {"ok": False, "error": "candidate not found"}
    conn.execute(
        "UPDATE era_candidates SET status = 'dismissed' WHERE candidate_id = ?",
        (candidate_id,),
    )
    conn.commit()
    return {"ok": True}
