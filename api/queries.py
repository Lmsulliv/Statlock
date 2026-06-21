"""Parameterized read queries: SQL in, raw count rows out. No statistics math
(that's stats/), no FastAPI. Shared by the API and the CLI through api.service.

Why direct SQL instead of the v_my_matchups / v_my_item_stats views: those
views group by era only and never expose a badge, so they cannot support the
rank-range slider. These queries keep the views' shape and add the badge and
game-mode predicates the presentation layer needs.

Rank-range basis: there is no per-player rank (api-findings #1); the only ranks
are the match-level team averages. Personal rows filter on the player's OWN
team's average badge -- the personal analogue of the baseline's match
team-average filter.
"""
import sqlite3

from api.scope import Scope


def resolve_self_account_id(conn: sqlite3.Connection) -> int | None:
    """The default account: the one flagged is_self. None if none is tracked."""
    row = conn.execute(
        "SELECT account_id FROM tracked_accounts WHERE is_self = 1"
        " ORDER BY account_id LIMIT 1"
    ).fetchone()
    return row["account_id"] if row else None


def list_tracked_accounts(conn: sqlite3.Connection) -> list[dict]:
    """Every tracked account (is_self first, then by id) for the account picker."""
    rows = conn.execute(
        "SELECT account_id, display_name, is_self FROM tracked_accounts"
        " ORDER BY is_self DESC, account_id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_tracked_account(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """One tracked account, or None if it isn't tracked (lets the namer 404)."""
    row = conn.execute(
        "SELECT account_id, display_name, is_self FROM tracked_accounts"
        " WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_snapshot_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(snapshot_id) AS s FROM baseline_snapshots").fetchone()
    return row["s"] if row and row["s"] is not None else None


def _era_clause(scope: Scope, column: str) -> tuple[str, list]:
    """Era predicate for PERSONAL rows. All-time (era_ids is None) means no
    filter; otherwise restrict to the chosen era ids."""
    if scope.era_ids is None:
        return "", []
    placeholders = ",".join("?" for _ in scope.era_ids)
    return f" AND {column} IN ({placeholders})", list(scope.era_ids)


def _baseline_era_ids(scope: Scope) -> tuple[int, ...]:
    """Era ids to SUM on the BASELINE side. All-time uses the era_id=0 sentinel
    (a single snapshot covering the whole timeline); a specific selection sums
    those eras' per-era baseline rows."""
    return (0,) if scope.era_ids is None else scope.era_ids


def _badge_clause(scope: Scope, team_column: str) -> tuple[str, list]:
    """Personal badge predicate on the player's own team average. Dropped at
    full range so NULL-badge matches still count (see Scope.is_full_badge_range)."""
    if scope.is_full_badge_range:
        return "", []
    expr = (
        f"(CASE WHEN {team_column} = 0 THEN m.average_badge_team0"
        f" ELSE m.average_badge_team1 END)"
    )
    return f" AND {expr} BETWEEN ? AND ?", [scope.badge_min, scope.badge_max]


# ── Personal aggregates ──────────────────────────────────────────────────────

def personal_matchups(conn: sqlite3.Connection, scope: Scope,
                      my_hero_id: int | None = None) -> list[dict]:
    """Personal record per (my hero, enemy hero) for the scoped account."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND me.hero_id = ?", [my_hero_id]
    # In-lane: keep only enemies in the SAME lane pair as me. Lane slots pair as
    # {1,2}/{3,4}/{5,6}; (lane+1)/2 is the integer group id (1,1,2,2,3,3), so this
    # captures both laners I faced, not a single 1v1. Personal side only -- the
    # global comparison switches to the same-lane baseline in baseline_matchups.
    lane_sql = (" AND (me.lane + 1) / 2 = (opp.lane + 1) / 2") if scope.in_lane else ""

    sql = (
        "SELECT me.hero_id AS my_hero, opp.hero_id AS enemy_hero,"
        " COUNT(*) AS games, SUM(me.won) AS wins"
        " FROM match_players me"
        " JOIN match_players opp"
        "   ON opp.match_id = me.match_id AND opp.team != me.team"
        " JOIN matches m ON m.match_id = me.match_id"
        " WHERE me.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql + hero_sql + lane_sql +
        " GROUP BY me.hero_id, opp.hero_id"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def personal_kill_trades(conn: sqlite3.Connection, scope: Scope,
                         my_hero_id: int | None = None) -> list[dict]:
    """Kill counts per enemy hero for the scoped account, in both directions:
    {enemy_hero, kills_by_you_on_them, kills_by_them_on_you}. The kill-trade twin
    of personal_matchups -- the same me/opponent self-join and the SAME scope
    clauses, so these counts line up with that query's games-faced figures -- but
    it also joins kill_events on the (me, opp) slot pair and tallies each
    direction. Killer/victim resolve to a hero through match_players by
    (match_id, player_slot), so an anonymized opponent (account_id = 0) still
    counts under the hero it piloted.

    Kept separate from personal_matchups on purpose: folding this kill_events join
    into that query would multiply its rows and corrupt its COUNT(*)/SUM(won)
    games-faced counts. An enemy hero faced but never traded with simply doesn't
    appear here, and the caller merges it back in as 0. NULL killer_slot rows
    (tower/creep) match neither slot condition, so non-player kills never count."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND me.hero_id = ?", [my_hero_id]
    lane_sql = (" AND (me.lane + 1) / 2 = (opp.lane + 1) / 2") if scope.in_lane else ""

    sql = (
        "SELECT opp.hero_id AS enemy_hero,"
        " SUM(CASE WHEN ke.killer_slot = me.player_slot"
        "          AND ke.victim_slot = opp.player_slot THEN 1 ELSE 0 END)"
        "   AS kills_by_you_on_them,"
        " SUM(CASE WHEN ke.killer_slot = opp.player_slot"
        "          AND ke.victim_slot = me.player_slot THEN 1 ELSE 0 END)"
        "   AS kills_by_them_on_you"
        " FROM match_players me"
        " JOIN match_players opp"
        "   ON opp.match_id = me.match_id AND opp.team != me.team"
        " JOIN matches m ON m.match_id = me.match_id"
        " JOIN kill_events ke ON ke.match_id = me.match_id"
        "   AND ((ke.killer_slot = me.player_slot AND ke.victim_slot = opp.player_slot)"
        "     OR (ke.killer_slot = opp.player_slot AND ke.victim_slot = me.player_slot))"
        " WHERE me.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql + hero_sql + lane_sql +
        " GROUP BY opp.hero_id"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def personal_item_stats(conn: sqlite3.Connection, scope: Scope,
                        hero_id: int) -> list[dict]:
    """Personal record per item for one hero for the scoped account."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    sql = (
        "SELECT ip.item_id AS item_id, COUNT(*) AS games, SUM(mp.won) AS wins,"
        " AVG(ip.purchase_time_s) AS avg_purchase_s"
        " FROM match_players mp"
        " JOIN match_item_purchases ip"
        "   ON ip.match_id = mp.match_id AND ip.player_slot = mp.player_slot"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND mp.hero_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql +
        " GROUP BY ip.item_id"
    )
    params = [scope.account_id, hero_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def account_results(conn: sqlite3.Connection, scope: Scope,
                    my_hero_id: int | None = None) -> list[dict]:
    """Every scoped match for the account as {match_id, start_time, won}, ordered
    oldest-first. This is the raw time-ordered stream tilt analysis groups into
    sessions (stats.sessions); the same era/badge/mode predicates as the other
    personal queries keep it consistent with what the rest of the app counts.

    `my_hero_id` optionally restricts to matches where the account played that
    hero -- recurring-player analysis uses it so the self-baseline matches the
    hero-filtered co-occurrence set (the rest of the app's "my hero" filter)."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND mp.hero_id = ?", [my_hero_id]
    sql = (
        "SELECT mp.match_id, m.start_time, mp.won"
        " FROM match_players mp"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql + hero_sql +
        " ORDER BY m.start_time ASC, mp.match_id ASC"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def recurring_co_players(conn: sqlite3.Connection, scope: Scope,
                         my_hero_id: int | None = None) -> list[dict]:
    """Every other player who shared the account's scoped matches, as
    {account_id, same_team, games, wins}. The structural twin of
    personal_matchups: a self-join of match_players on the same match, but keyed
    on the OTHER player's account_id instead of their hero, and split by whether
    they were on my team (same_team=1) or against me (0) rather than always
    opponents. `wins` sums me.won -- the same value for every row of a match --
    so it counts the shared games I won (with that teammate / against that
    opponent). No co-occurrence floor here: stats.recurring owns that gate."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND me.hero_id = ?", [my_hero_id]

    # Exclude anonymized players (account_id = 0) on the OTHER side only. Two
    # reasons: an account_id = 0 co-player is not a real recurring person, and --
    # because account_id is no longer unique within a match -- all of a lobby's
    # zeros would otherwise collapse into one GROUP BY other.account_id bucket,
    # inflating its games/wins. This is deliberately NOT done where hero identity
    # is what's counted (personal_matchups): an anonymized opponent still played a
    # known hero and must count toward matchups.
    sql = (
        "SELECT other.account_id AS account_id,"
        " (other.team = me.team) AS same_team,"
        " COUNT(*) AS games, SUM(me.won) AS wins"
        " FROM match_players me"
        " JOIN match_players other"
        "   ON other.match_id = me.match_id AND other.account_id != me.account_id"
        "   AND other.account_id != 0"
        " JOIN matches m ON m.match_id = me.match_id"
        " WHERE me.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql + hero_sql +
        " GROUP BY other.account_id, same_team"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── Global baselines (re-summed across stored brackets) ──────────────────────

def baseline_matchups(conn: sqlite3.Connection, scope: Scope,
                      snapshot_id: int) -> dict[tuple[int, int], dict]:
    """SUM(wins)/SUM(matches) per (hero, enemy) across every stored bracket
    contained in the requested badge range and across the scoped eras.
    Keyed by (hero_id, enemy_hero_id)."""
    era_ids = _baseline_era_ids(scope)
    era_ph = ",".join("?" for _ in era_ids)
    # In-lane compares against the same-lane (laning-phase) baseline; otherwise
    # the overall one. The two never mix (same_lane is part of the PK).
    same_lane = 1 if scope.in_lane else 0
    sql = (
        "SELECT hero_id, enemy_hero_id, SUM(wins) AS wins, SUM(matches) AS matches"
        " FROM baseline_hero_matchups"
        " WHERE snapshot_id = ?"
        f"   AND era_id IN ({era_ph})"
        "   AND badge_min >= ? AND badge_max <= ?"
        "   AND same_lane = ?"
        " GROUP BY hero_id, enemy_hero_id"
    )
    params = [snapshot_id, *era_ids, scope.badge_min, scope.badge_max, same_lane]
    out: dict[tuple[int, int], dict] = {}
    for r in conn.execute(sql, params).fetchall():
        out[(r["hero_id"], r["enemy_hero_id"])] = {"wins": r["wins"], "matches": r["matches"]}
    return out


def baseline_item_stats(conn: sqlite3.Connection, scope: Scope, hero_id: int,
                        snapshot_id: int) -> dict[int, dict]:
    """SUM(wins)/SUM(matches) per item for one hero, re-summed across the
    bracket subset and eras. avg_purchase_s is matches-weighted across brackets.
    Keyed by item_id."""
    era_ids = _baseline_era_ids(scope)
    era_ph = ",".join("?" for _ in era_ids)
    sql = (
        "SELECT item_id, SUM(wins) AS wins, SUM(matches) AS matches,"
        " SUM(avg_purchase_s * matches) / NULLIF(SUM(matches), 0) AS avg_purchase_s"
        " FROM baseline_hero_item_stats"
        " WHERE snapshot_id = ? AND hero_id = ?"
        f"   AND era_id IN ({era_ph})"
        "   AND badge_min >= ? AND badge_max <= ?"
        " GROUP BY item_id"
    )
    params = [snapshot_id, hero_id, *era_ids, scope.badge_min, scope.badge_max]
    out: dict[int, dict] = {}
    for r in conn.execute(sql, params).fetchall():
        out[r["item_id"]] = {
            "wins": r["wins"], "matches": r["matches"],
            "avg_purchase_s": r["avg_purchase_s"],
        }
    return out


# ── Reference name lookups ───────────────────────────────────────────────────

def hero_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["hero_id"]: r["name"] for r in
            conn.execute("SELECT hero_id, name FROM heroes").fetchall()}


def hero_images(conn: sqlite3.Connection) -> dict[int, str | None]:
    """hero_id -> icon URL (heroes.image_url, from assets images.icon_hero_card).
    May be None for heroes whose asset row has no image."""
    return {r["hero_id"]: r["image_url"] for r in
            conn.execute("SELECT hero_id, image_url FROM heroes").fetchall()}


def item_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["item_id"]: r["name"] for r in
            conn.execute("SELECT item_id, name FROM items").fetchall()}


# ── Name resolution (manual label > Steam persona > bare account id) ─────────

# owner_id 0 is the GLOBAL_OWNER sentinel: one shared namespace of manual labels
# today, real user ids later with no schema change. resolve_names() and the rename
# writes (api.service) both default to it -- the single per-user seam.
GLOBAL_OWNER = 0


def _labels_for(conn: sqlite3.Connection, account_ids: list[int],
                owner_id: int) -> dict[int, str]:
    """{account_id: display_name} from account_labels for one owner, restricted to
    the requested ids."""
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    rows = conn.execute(
        f"SELECT account_id, display_name FROM account_labels"
        f" WHERE owner_id = ? AND account_id IN ({placeholders})",
        [owner_id, *account_ids],
    ).fetchall()
    return {r["account_id"]: r["display_name"] for r in rows}


def _persona_names(conn: sqlite3.Connection, account_ids: list[int]) -> dict[int, str]:
    """{account_id: persona_name} from steam_personas, skipping NULL placeholders
    (private/unresolved profiles must lose to the bare-id fallback)."""
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    rows = conn.execute(
        f"SELECT account_id, persona_name FROM steam_personas"
        f" WHERE account_id IN ({placeholders}) AND persona_name IS NOT NULL",
        list(account_ids),
    ).fetchall()
    return {r["account_id"]: r["persona_name"] for r in rows}


def resolve_names(conn: sqlite3.Connection, account_ids, owner_id: int = GLOBAL_OWNER
                  ) -> dict[int, str]:
    """{account_id: name} for every requested id, with precedence: this owner's
    manual label > the global-0 label > steam_personas.persona_name >
    str(account_id). Every id resolves to a string (never None), so callers can
    surface co-players and opponents -- mostly untracked -- by their best name."""
    ids = list(dict.fromkeys(account_ids))   # dedupe, preserve order
    owner_labels = _labels_for(conn, ids, owner_id)
    global_labels = (owner_labels if owner_id == GLOBAL_OWNER
                     else _labels_for(conn, ids, GLOBAL_OWNER))
    personas = _persona_names(conn, ids)
    return {
        aid: (owner_labels.get(aid) or global_labels.get(aid)
              or personas.get(aid) or str(aid))
        for aid in ids
    }


def item_images(conn: sqlite3.Connection) -> dict[int, str | None]:
    """item_id -> shop-art URL (items.image_url, from the assets loader).
    May be None for items whose asset row has no image."""
    return {r["item_id"]: r["image_url"] for r in
            conn.execute("SELECT item_id, image_url FROM items").fetchall()}


def list_ranks(conn: sqlite3.Connection) -> list[dict]:
    """Rank tiers ordered low to high (name + color; art derived in service)."""
    return [dict(r) for r in
            conn.execute("SELECT tier, name, color FROM ranks ORDER BY tier").fetchall()]


# ── Overview / sync / eras ───────────────────────────────────────────────────

def mmr_series(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT arh.match_id, arh.badge, m.start_time"
        " FROM account_rank_history arh"
        " JOIN matches m ON m.match_id = arh.match_id"
        " WHERE arh.account_id = ?"
        " ORDER BY m.start_time",
        (account_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def last_matches(conn: sqlite3.Connection, account_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT mp.match_id, mp.hero_id, mp.won, mp.kills, mp.deaths, mp.assists,"
        " mp.net_worth, m.start_time, m.game_mode"
        " FROM match_players mp"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ?"
        " ORDER BY m.start_time DESC, mp.match_id DESC"
        " LIMIT ?",
        (account_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def match_core(conn: sqlite3.Connection, match_id: int) -> dict | None:
    """The match-level row for the detail view, raw_json included so the service
    can parse the roster and death feed out of it. None if no such match."""
    row = conn.execute(
        "SELECT match_id, start_time, duration_s, game_mode, winning_team,"
        " average_badge_team0, average_badge_team1, era_id, raw_json"
        " FROM matches WHERE match_id = ?",
        (match_id,),
    ).fetchone()
    return dict(row) if row else None


def match_purchases(conn: sqlite3.Connection, match_id: int,
                    player_slot: int) -> list[dict]:
    """One player's item purchases in a match, ordered by buy time. Keyed on
    player_slot, not account_id: with anonymized players a match can hold several
    account_id = 0 rows, so the slot is the only key that isolates one player's
    buys. Already filtered to real shop items at ingest, so no upgrade/ability
    rows leak in."""
    rows = conn.execute(
        "SELECT item_id, purchase_time_s, sold_time_s"
        " FROM match_item_purchases WHERE match_id = ? AND player_slot = ?"
        " ORDER BY purchase_time_s",
        (match_id, player_slot),
    ).fetchall()
    return [dict(r) for r in rows]


def match_kill_trades(conn: sqlite3.Connection, match_id: int) -> list[dict]:
    """Per-(killer_slot, victim_slot) kill counts for one match, read straight
    from kill_events, as {killer_slot, victim_slot, n}. Slot-keyed, so it
    attributes kills even to anonymized opponents (account_id = 0) whose slot is
    still unique within the match. NULL-killer rows (tower/creep) are returned as
    stored; the service never looks them up, since a trade needs two players."""
    rows = conn.execute(
        "SELECT killer_slot, victim_slot, COUNT(*) AS n"
        " FROM kill_events WHERE match_id = ?"
        " GROUP BY killer_slot, victim_slot",
        (match_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM fetch_queue GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def last_discovery_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(last_synced_at) AS t FROM sync_state").fetchone()
    return row["t"] if row else None


def last_maintenance_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM worker_meta WHERE key = 'last_maintenance_at'"
    ).fetchone()
    return row["value"] if row else None


def pending_candidate_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM era_candidates WHERE status = 'pending'"
    ).fetchone()["n"]


def list_eras(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT era_id, label, started_at FROM patch_eras ORDER BY started_at"
    ).fetchall()
    return [dict(r) for r in rows]


def list_pending_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT candidate_id, post_url, post_title, posted_at, change_lines, score, status"
        " FROM era_candidates WHERE status = 'pending' ORDER BY posted_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_candidate(conn: sqlite3.Connection, candidate_id: int) -> dict | None:
    row = conn.execute(
        "SELECT candidate_id, post_url, post_title, posted_at, status"
        " FROM era_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return dict(row) if row else None
