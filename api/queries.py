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
from ingest.util import DEFAULT_USER_ID
from stats.laning import LANE_END_S


def resolve_self_account_id(conn: sqlite3.Connection,
                            user_id: int = DEFAULT_USER_ID) -> int | None:
    """The user's default account: the one they flagged is_self (on user_accounts).
    None if they have none. user_id defaults to the local/dev user (Phase 1)."""
    row = conn.execute(
        "SELECT account_id FROM user_accounts WHERE user_id = ? AND is_self = 1"
        " ORDER BY account_id LIMIT 1",
        (user_id,),
    ).fetchone()
    return row["account_id"] if row else None


def list_tracked_accounts(conn: sqlite3.Connection,
                          user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """The user's accounts (is_self first, then by id) for the account picker.
    display_name comes from tracked_accounts; is_self from the per-user link."""
    rows = conn.execute(
        "SELECT ua.account_id, ta.display_name, ua.is_self"
        " FROM user_accounts ua"
        " JOIN tracked_accounts ta ON ta.account_id = ua.account_id"
        " WHERE ua.user_id = ?"
        " ORDER BY ua.is_self DESC, ua.account_id",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_tracked_account(conn: sqlite3.Connection, account_id: int,
                        user_id: int = DEFAULT_USER_ID) -> dict | None:
    """One of the user's accounts, or None if they don't track it (lets the namer
    404). is_self comes from the per-user link, display_name from tracked_accounts."""
    row = conn.execute(
        "SELECT ua.account_id, ta.display_name, ua.is_self"
        " FROM user_accounts ua"
        " JOIN tracked_accounts ta ON ta.account_id = ua.account_id"
        " WHERE ua.account_id = ? AND ua.user_id = ?",
        (account_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def latest_snapshot_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(snapshot_id) AS s FROM baseline_snapshots").fetchone()
    return row["s"] if row and row["s"] is not None else None


def baseline_version(conn: sqlite3.Connection) -> tuple:
    """A cheap token that changes whenever the baseline data the read layer sees
    could have changed. Used to invalidate the in-process baseline cache.

    Two signals are needed, not one: a brand-new snapshot bumps MAX(snapshot_id),
    but the nightly staggered refresh keeps the SAME snapshot_id and only rewrites
    the due eras' rows in place (ingest.maintenance.refresh_baselines), recording
    the fetch in baseline_refresh_state.last_refreshed_at. So a snapshot-id-only
    token would miss a staggered refresh and serve stale baselines; pairing it
    with MAX(last_refreshed_at) catches both. Both are single-row aggregates over
    small tables -- cheap to read on every request."""
    snap = conn.execute("SELECT MAX(snapshot_id) AS s FROM baseline_snapshots").fetchone()
    refreshed = conn.execute(
        "SELECT MAX(last_refreshed_at) AS r FROM baseline_refresh_state").fetchone()
    return (snap["s"] if snap else None, refreshed["r"] if refreshed else None)


def ingest_version(conn: sqlite3.Connection) -> tuple:
    """A cheap token that changes whenever a new match has been ingested. Used to
    invalidate the LIVE baseline cache (baseline_performance / baseline_laning),
    which AVGs over every match_players / laning_stats row rather than over the
    stored snapshot tables, so it moves with ingestion, not the nightly refresh.

    MAX(match_id) is O(1) on the INTEGER PRIMARY KEY and advances on every
    ingested match; because one match is one transaction (hard rule 4), the
    match_players / laning_stats / kill_events rows a live baseline reads land
    together with the matches row this token watches. Known gap: a
    `reprocess-archive` backfill rewrites kill_events (lane_deaths) WITHOUT moving
    MAX(match_id), so the live cache would keep serving pre-backfill lane_deaths
    means until the paired TTL floor lapses or a real match ingests -- an
    acceptable bound for a population mean over thousands of games."""
    row = conn.execute("SELECT MAX(match_id) AS m FROM matches").fetchone()
    return (row["m"] if row else None,)


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


def _badge_clause(scope: Scope, team_column: str,
                  badge_table: str = "m") -> tuple[str, list]:
    """Personal badge predicate on the player's own team average. Dropped at
    full range so NULL-badge matches still count (see Scope.is_full_badge_range).

    `badge_table` is the alias carrying average_badge_team0/1: the `matches` join
    alias ("m") for the metadata-only queries, or the v_account_matches alias for
    the view-backed ones (whose summary rows have NULL badge columns, so a
    narrowed range drops them -- exactly the honest behaviour we want)."""
    if scope.is_full_badge_range:
        return "", []
    expr = (
        f"(CASE WHEN {team_column} = 0 THEN {badge_table}.average_badge_team0"
        f" ELSE {badge_table}.average_badge_team1 END)"
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


# ── Death patterns (kill_events aggregated across the scoped match set) ───────

def death_by_enemy_hero(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Per enemy hero across the scoped matches: {enemy_hero, games_faced, deaths}
    -- how often each enemy hero you faced was the one that killed you.

    Modeled on personal_kill_trades' me/opponent self-join with the SAME scope
    clauses, but a LEFT JOIN to kill_events on the (killer = opp, victim = me)
    slot pair, so an enemy hero you faced but never died to still appears at 0
    deaths. COUNT(DISTINCT me.match_id) is robust to the same enemy hero showing
    up on two enemy players in one match. Killer/victim resolve to a hero through
    match_players by (match_id, player_slot), so an anonymized opponent
    (account_id = 0) still counts under the hero it piloted. A NULL killer_slot
    (tower / creep) matches no opp.player_slot, so environment deaths are excluded
    from the by-hero ranking -- there is no hero to attribute them to. Raw counts
    only; no verdict (there is no stored per-matchup death baseline)."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    sql = (
        "SELECT opp.hero_id AS enemy_hero,"
        " COUNT(DISTINCT me.match_id) AS games_faced,"
        " SUM(CASE WHEN ke.event_id IS NOT NULL THEN 1 ELSE 0 END) AS deaths"
        " FROM match_players me"
        " JOIN match_players opp"
        "   ON opp.match_id = me.match_id AND opp.team != me.team"
        " JOIN matches m ON m.match_id = me.match_id"
        " LEFT JOIN kill_events ke ON ke.match_id = me.match_id"
        "   AND ke.killer_slot = opp.player_slot AND ke.victim_slot = me.player_slot"
        " WHERE me.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql +
        " GROUP BY opp.hero_id"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def damage_taken_by_enemy_hero(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Per enemy hero across the scoped matches: {enemy_hero, games_faced,
    total_damage} -- how much GROSS damage each enemy hero you faced dealt TO you
    (damage_matrix, materialized into damage_taken_sources).

    The twin of death_by_enemy_hero: the same me/opponent self-join and scope
    clauses, but a LEFT JOIN to damage_taken_sources on the (source = opp,
    victim = me) slot pair, so an enemy hero you faced but who never damaged you
    still appears at 0. COUNT(DISTINCT me.match_id) counts games faced (robust to
    the same hero on two enemy players in one match); SUM(dts.damage_taken) is the
    gross total, which the service divides by games faced for an average. Source
    resolves to a hero through match_players by (match_id, player_slot), so an
    anonymized opponent (account_id = 0) still counts under the hero it piloted. A
    NULL source_slot (environment) matches no opp.player_slot, so non-hero damage
    is excluded. Raw, GROSS, RELATIVE totals only; no verdict and no baseline
    (api-findings: damage_matrix is pre-mitigation and does not reconcile with the
    net damage-taken total)."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    sql = (
        "SELECT opp.hero_id AS enemy_hero,"
        " COUNT(DISTINCT me.match_id) AS games_faced,"
        " SUM(dts.damage_taken) AS total_damage"
        " FROM match_players me"
        " JOIN match_players opp"
        "   ON opp.match_id = me.match_id AND opp.team != me.team"
        " JOIN matches m ON m.match_id = me.match_id"
        " LEFT JOIN damage_taken_sources dts ON dts.match_id = me.match_id"
        "   AND dts.source_slot = opp.player_slot AND dts.victim_slot = me.player_slot"
        " WHERE me.account_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql +
        " GROUP BY opp.hero_id"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scoped_match_count(conn: sqlite3.Connection, scope: Scope) -> int:
    """How many scoped games the account played -- the zero-fill denominator for
    the death timeline. Same era/badge/game-mode predicates and the duration_s > 0
    guard as personal_performance, so it counts exactly the games the population
    death baseline does."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    sql = (
        "SELECT COUNT(*) AS n"
        " FROM match_players mp"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ? AND m.duration_s > 0"
        + era_sql + badge_sql
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    row = conn.execute(sql, params).fetchone()
    return row["n"] if row else 0


def personal_death_times(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """The account's deaths as {match_id, game_time_s}, one row per kill_event in
    which the account was the victim. Untimed deaths (game_time_s IS NULL) are
    dropped -- they can't be placed on the timeline. Same scope predicates and the
    duration_s > 0 guard as scoped_match_count, so every returned match is one of
    the counted games."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "me.team")
    sql = (
        "SELECT me.match_id AS match_id, ke.game_time_s AS game_time_s"
        " FROM match_players me"
        " JOIN kill_events ke ON ke.match_id = me.match_id"
        "   AND ke.victim_slot = me.player_slot"
        " JOIN matches m ON m.match_id = me.match_id"
        " WHERE me.account_id = ? AND m.game_mode = ? AND m.duration_s > 0"
        "   AND ke.game_time_s IS NOT NULL"
        + era_sql + badge_sql
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def population_death_timeline(conn: sqlite3.Connection,
                             scope: Scope) -> tuple[int, dict[int, int]]:
    """The live "field" death baseline, the timeline twin of baseline_performance:
    (population_games, {minute: deaths}) computed straight from kill_events +
    match_players over the same era/badge/game-mode scope. The scoped account is
    excluded (v.account_id != ?) so the comparison is "you vs the field"; each
    population row is badge-scoped by its OWN team average, exactly like the
    personal side. `minute` is the uncapped game-minute (game_time_s / 60); the
    pure stats.deaths layer folds the long tail into the trailing bin so the cap
    lives in one place. population_games counts the same scoped player-games as
    the deaths numerator, so deaths/games is an honest per-game death rate."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "v.team")
    params = [scope.account_id, scope.game_mode] + era_params + badge_params

    games_sql = (
        "SELECT COUNT(*) AS n"
        " FROM match_players v"
        " JOIN matches m ON m.match_id = v.match_id"
        " WHERE v.account_id != ? AND m.game_mode = ? AND m.duration_s > 0"
        + era_sql + badge_sql
    )
    games_row = conn.execute(games_sql, params).fetchone()
    population_games = games_row["n"] if games_row else 0

    deaths_sql = (
        "SELECT (ke.game_time_s / 60) AS minute, COUNT(*) AS deaths"
        " FROM kill_events ke"
        " JOIN match_players v ON v.match_id = ke.match_id"
        "   AND v.player_slot = ke.victim_slot"
        " JOIN matches m ON m.match_id = ke.match_id"
        " WHERE v.account_id != ? AND m.game_mode = ? AND m.duration_s > 0"
        "   AND ke.game_time_s IS NOT NULL"
        + era_sql + badge_sql +
        " GROUP BY minute"
    )
    by_minute = {r["minute"]: r["deaths"]
                 for r in conn.execute(deaths_sql, params).fetchall()}
    return population_games, by_minute


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


def personal_ability_points(conn: sqlite3.Connection, scope: Scope,
                            hero_id: int) -> list[dict]:
    """Every ability point the scoped account spent on one hero, as {match_id, won,
    ability_id, point_number}, ordered by match then skill-up order. The won flag
    comes from joining match_players (the played hero's win/loss), so the stats
    layer can split the skill order by result. Same era/badge/game_mode predicates
    as personal_item_stats keep it consistent with the rest of the app."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    sql = (
        "SELECT ae.match_id AS match_id, mp.won AS won, ae.ability_id AS ability_id,"
        " ae.point_number AS point_number"
        " FROM match_players mp"
        " JOIN ability_events ae"
        "   ON ae.match_id = mp.match_id AND ae.player_slot = mp.player_slot"
        " JOIN matches m ON m.match_id = mp.match_id"
        " WHERE mp.account_id = ? AND mp.hero_id = ? AND m.game_mode = ?"
        + era_sql + badge_sql +
        " ORDER BY ae.match_id, ae.point_number"
    )
    params = [scope.account_id, hero_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def account_results(conn: sqlite3.Connection, scope: Scope,
                    my_hero_id: int | None = None,
                    full_only: bool = True) -> list[dict]:
    """Every scoped match for the account as {match_id, start_time, won, source},
    ordered oldest-first. This is the raw time-ordered stream tilt analysis groups
    into sessions (stats.sessions); the same era/badge/mode predicates as the other
    personal queries keep it consistent with what the rest of the app counts.

    `my_hero_id` optionally restricts to matches where the account played that
    hero -- recurring-player analysis uses it so the self-baseline matches the
    hero-filtered co-occurrence set (the rest of the app's "my hero" filter).

    Reads v_account_matches. `full_only` controls whether provisional summaries
    are included: tilt (which only needs win/loss and match time) passes False so
    a fresh import gets a session view immediately, while recurring-players keeps
    the default True so its self-baseline stays aligned with its metadata-only
    co-player counts (recurring can't see co-players from a summary). Either way a
    summary row with an unknown result is dropped (`won IS NOT NULL`): it can't
    join a win/loss stream without being silently miscounted as a loss."""
    era_sql, era_params = _era_clause(scope, "mp.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team", badge_table="mp")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND mp.hero_id = ?", [my_hero_id]
    full_sql = " AND mp.source = 'full'" if full_only else ""
    sql = (
        "SELECT mp.match_id, mp.start_time, mp.won, mp.source"
        " FROM v_account_matches mp"
        " WHERE mp.account_id = ? AND mp.game_mode = ? AND mp.won IS NOT NULL"
        + full_sql + era_sql + badge_sql + hero_sql +
        " ORDER BY mp.start_time ASC, mp.match_id ASC"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def hero_record_rows(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """Per-hero win/loss record for the scoped account, as {hero_id, games, wins,
    provisional}. Reads v_account_matches so a fresh import gets a hero list from
    its provisional summaries; `provisional` is 1 for a hero any of whose
    contributing rows is summary-backed (MAX over the boolean), so the service can
    flag heroes still awaiting metadata. Same era/badge/game-mode predicates as the
    rest of the personal reads. A summary row with an unknown result (won IS NULL)
    can't be scored win/loss and is dropped; a NULL hero_id (shouldn't happen) is
    excluded so it never forms a bogus bucket."""
    era_sql, era_params = _era_clause(scope, "mp.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team", badge_table="mp")
    sql = (
        "SELECT mp.hero_id AS hero_id, COUNT(*) AS games, SUM(mp.won) AS wins,"
        " MAX(mp.source = 'summary') AS provisional"
        " FROM v_account_matches mp"
        " WHERE mp.account_id = ? AND mp.game_mode = ?"
        "   AND mp.won IS NOT NULL AND mp.hero_id IS NOT NULL"
        + era_sql + badge_sql +
        " GROUP BY mp.hero_id"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
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

    # In-lane: keep only co-players in the SAME lane pair as me. Same lane-pair
    # predicate personal_matchups uses ({1,2}/{3,4}/{5,6}; (lane+1)/2 is the group
    # id), but team-agnostic on purpose -- it keeps any co-player who shared my lane
    # pairing whether teammate or opponent, so a shared-game count drops to just the
    # lane-pair games. When in_lane is false this is empty and behaviour is as before.
    # The self-baseline is deliberately NOT lane-filtered here: it's account_results
    # (the service's overall win rate over the same matches), and in_lane changes only
    # which co-players count inside those matches, not which matches I played -- so
    # account_results stays scope-wide. Don't "fix" it to match this predicate.
    lane_sql = (" AND (me.lane + 1) / 2 = (other.lane + 1) / 2") if scope.in_lane else ""

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
        + era_sql + badge_sql + hero_sql + lane_sql +
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


# ── Continuous-metric performance ────────────────────────────────────────────
#
# The single source of truth for the continuous metrics surfaced on the
# Performance screen. Every SQL expr references ONLY the `mp` alias, so one
# formula serves both the personal per-match values and the population AVG -- and
# the same expr reads correctly whether `mp` is match_players (metadata) or the
# v_account_matches view (which carries duration_s too, so net worth / min needs
# no `matches` join). `label` and `higher_is_better` are presentation metadata,
# not statistics math: the verdict itself is computed in stats/ (CLAUDE.md hard
# rule 1); higher_is_better only tells the service which direction counts as
# "good" (see api.service).
PERF_METRICS = [
    {"key": "net_worth_per_min", "label": "Net worth / min",
     "expr": "mp.net_worth * 60.0 / mp.duration_s", "higher_is_better": True},
    {"key": "kills", "label": "Kills", "expr": "mp.kills", "higher_is_better": True},
    {"key": "deaths", "label": "Deaths", "expr": "mp.deaths", "higher_is_better": False},
    {"key": "assists", "label": "Assists", "expr": "mp.assists", "higher_is_better": True},
    {"key": "last_hits", "label": "Last hits", "expr": "mp.last_hits", "higher_is_better": True},
    {"key": "denies", "label": "Denies", "expr": "mp.denies", "higher_is_better": True},
    {"key": "player_damage", "label": "Player damage",
     "expr": "mp.player_damage", "higher_is_better": True},
    {"key": "obj_damage", "label": "Obj damage",
     "expr": "mp.obj_damage", "higher_is_better": True},
    {"key": "healing", "label": "Healing", "expr": "mp.healing", "higher_is_better": True},
    {"key": "player_damage_taken", "label": "Damage taken",
     "expr": "mp.player_damage_taken", "higher_is_better": False},
]


def personal_performance(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """One row per scoped match the account played: hero_id, source, plus each
    metric's per-match value (PERF_METRICS). Raw rows, not aggregates -- the
    service buckets them per hero and overall and hands the value lists to
    stats.mean_interval / mean_verdict (which need the spread, not just a mean).

    Reads v_account_matches, so a freshly imported account's provisional history
    summaries feed the screen before full metadata is drained (and drop out under
    a narrowed era/badge scope, whose NULL columns fail the predicates). The
    duration guard allows NULL (a summary row may not know its duration): the row
    still contributes its known metrics, and net-worth-per-minute is NULL for it
    (x / NULL) rather than a fabricated number. Same era/badge/game-mode
    predicates as personal_matchups."""
    era_sql, era_params = _era_clause(scope, "mp.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team", badge_table="mp")
    select = ", ".join(f"({mdef['expr']}) AS {mdef['key']}" for mdef in PERF_METRICS)
    sql = (
        f"SELECT mp.hero_id AS hero_id, mp.source AS source, {select}"
        " FROM v_account_matches mp"
        " WHERE mp.account_id = ? AND mp.game_mode = ?"
        "   AND (mp.duration_s IS NULL OR mp.duration_s > 0)"
        + era_sql + badge_sql
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def performance_series(conn: sqlite3.Connection, scope: Scope,
                       my_hero_id: int | None = None) -> list[dict]:
    """The time-ordered twin of personal_performance: one row per scoped match,
    carrying start_time and won alongside each metric's per-match value, ordered
    oldest-first. Trends buckets this single stream into rolling windows and
    calendar buckets (stats.trends), so win rate and the continuous metrics are
    always aligned to the same matches. Same era/badge/game-mode predicates and
    the same duration guard as personal_performance; `my_hero_id` optionally
    restricts to one hero, the trends analogue of the matchups perspective.

    Reads v_account_matches like personal_performance, so summaries feed the
    series too. `AND mp.won IS NOT NULL` drops a summary row whose result is
    unknown: the win-rate series sums `won`, and a NULL there can neither win nor
    lose a bucket -- it must not silently count as a loss."""
    era_sql, era_params = _era_clause(scope, "mp.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team", badge_table="mp")
    hero_sql, hero_params = ("", [])
    if my_hero_id is not None:
        hero_sql, hero_params = " AND mp.hero_id = ?", [my_hero_id]
    select = ", ".join(f"({mdef['expr']}) AS {mdef['key']}" for mdef in PERF_METRICS)
    sql = (
        f"SELECT mp.match_id AS match_id, mp.start_time AS start_time,"
        f" mp.hero_id AS hero_id, mp.won AS won, mp.source AS source, {select}"
        " FROM v_account_matches mp"
        " WHERE mp.account_id = ? AND mp.game_mode = ?"
        "   AND (mp.duration_s IS NULL OR mp.duration_s > 0)"
        "   AND mp.won IS NOT NULL"
        + era_sql + badge_sql + hero_sql +
        " ORDER BY mp.start_time ASC, mp.match_id ASC"
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params + hero_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def baseline_performance(conn: sqlite3.Connection, scope: Scope,
                         my_hero_ids: list[int]) -> dict:
    """Population mean of each continuous metric, computed live from match_players.

    There is no stored continuous baseline (the baseline_* snapshot tables carry
    only win/loss + item timing; see docs/data-model.md), but every player's
    per-match stats are ingested, so "the typical player at this scope" is just an
    AVG over the same era/badge/game-mode predicates. The scoped account itself is
    excluded so the comparison is "you vs the field" -- the live analogue of the
    snapshot baselines being external to you. Each population row is badge-scoped
    by its OWN team average, exactly like the personal side.

    Returns {hero_id: {"n": games, <metric_key>: mean_or_None, ...}} plus an
    "overall" entry pooled across exactly the heroes you played (my_hero_ids) --
    the continuous analogue of matchups()'s hero-mix-matched overall baseline.
    A metric that is NULL for the whole population comes back as None, which the
    service reads as "no baseline" and shows personal-only.

    Reads v_account_matches restricted to source = 'full': a population baseline
    must rest only on real ingested metadata, never on another account's
    provisional summary (which carries no damage/badge context anyway). That
    `source = 'full'` slice is byte-for-byte the old match_players JOIN matches, so
    baseline numbers are unchanged; the view is used only so PERF_METRICS' single
    duration expr (mp.duration_s) resolves here exactly as on the personal side."""
    era_sql, era_params = _era_clause(scope, "mp.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team", badge_table="mp")
    avg_select = ", ".join(f"AVG({mdef['expr']}) AS {mdef['key']}" for mdef in PERF_METRICS)
    base_from = (
        " FROM v_account_matches mp"
        " WHERE mp.source = 'full' AND mp.account_id != ? AND mp.game_mode = ?"
        "   AND mp.duration_s > 0"
        + era_sql + badge_sql
    )
    base_params = [scope.account_id, scope.game_mode] + era_params + badge_params

    out: dict = {}
    per_hero_sql = (f"SELECT mp.hero_id AS hero_id, COUNT(*) AS n, {avg_select}"
                    + base_from + " GROUP BY mp.hero_id")
    for r in conn.execute(per_hero_sql, base_params).fetchall():
        out[r["hero_id"]] = {k: r[k] for k in r.keys() if k != "hero_id"}

    if my_hero_ids:
        hero_ph = ",".join("?" for _ in my_hero_ids)
        overall_sql = (f"SELECT COUNT(*) AS n, {avg_select}"
                       + base_from + f" AND mp.hero_id IN ({hero_ph})")
        row = conn.execute(overall_sql, base_params + list(my_hero_ids)).fetchone()
        if row is not None:
            out["overall"] = {k: row[k] for k in row.keys()}
    return out


# ── Laning (early-game continuous metrics at the lane-end mark) ───────────────

# The laning analogue of PERF_METRICS: the early-game numbers the laning report
# compares, read off the lane-end snapshot materialized in laning_stats (the `ls`
# alias both queries below use). Unlike PERF_METRICS these are RAW cumulative
# values, not per-minute -- every player is read at ~the same fixed mark
# (LANE_END_S), so the values are directly comparable without normalizing by time.
# net worth at end of laning is the headline; last hits (the snapshot's
# creep_kills) and denies are the supporting laning fundamentals. label /
# higher_is_better are presentation metadata; the verdict math lives in stats/.
#
# lane_deaths is the combat half of "did you lose your lane" and the one metric not
# read off the laning_stats snapshot: it counts kill_events where you were the
# victim of a lane-pair opponent (opposite team, same (lane+1)/2 pairing -- the
# app's "in lane" rule everywhere else) at or before LANE_END_S. Its expr is a
# correlated subquery over the outer ls (match_id, player_slot) and mp (team, lane)
# aliases that BOTH personal_laning and baseline_laning expose, so it rides the
# same select-building loops with no special-casing. COUNT(*) is never NULL, so a
# played match with no qualifying death contributes a real 0 (a player with no
# laning_stats row stays absent); the baseline AVGs that same count over the field
# for an honest per-game comparison -- never a fabricated 0. NULL killer_slot
# (tower/creep) and untimed deaths (game_time_s IS NULL) fail the join/predicate
# and are excluded -- neither can be attributed to a lane opponent. LANE_END_S is
# inlined as an int literal (a trusted stats/ constant, not user input): the shared
# loops concatenate exprs without per-metric bind params, so a placeholder here
# would desync the parameter list.
LANING_METRICS = [
    {"key": "net_worth", "label": "Net worth @ lane end",
     "expr": "ls.net_worth", "higher_is_better": True},
    {"key": "last_hits", "label": "Last hits @ lane end",
     "expr": "ls.last_hits", "higher_is_better": True},
    {"key": "denies", "label": "Denies @ lane end",
     "expr": "ls.denies", "higher_is_better": True},
    {"key": "lane_deaths", "label": "Lane deaths @ lane end",
     "expr": (
         "(SELECT COUNT(*) FROM kill_events ke"
         "  JOIN match_players opp ON opp.match_id = ke.match_id"
         "    AND opp.player_slot = ke.killer_slot"
         "  WHERE ke.match_id = ls.match_id"
         "    AND ke.victim_slot = ls.player_slot"
         "    AND opp.team != mp.team"
         "    AND (opp.lane + 1) / 2 = (mp.lane + 1) / 2"
         f"    AND ke.game_time_s <= {LANE_END_S})"),
     "higher_is_better": False},
]


def personal_laning(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """One row per scoped match the account played, carrying hero_id plus each
    laning metric's value at the lane-end snapshot (LANING_METRICS). The laning
    twin of personal_performance: raw rows, not aggregates -- the service buckets
    them per hero and overall and hands the value lists to stats.mean_interval /
    mean_verdict. laning_stats holds only the snapshot values, so hero/account/
    team come from match_players joined on (match_id, player_slot); same era/badge/
    game-mode predicates as personal_performance. A match with no lane-end snapshot
    simply has no laning_stats row, so it drops out honestly."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    select = ", ".join(f"({mdef['expr']}) AS {mdef['key']}" for mdef in LANING_METRICS)
    sql = (
        f"SELECT mp.hero_id AS hero_id, {select}"
        " FROM laning_stats ls"
        " JOIN match_players mp ON mp.match_id = ls.match_id"
        "   AND mp.player_slot = ls.player_slot"
        " JOIN matches m ON m.match_id = ls.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ? AND m.duration_s > 0"
        + era_sql + badge_sql
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def personal_laning_outcomes(conn: sqlite3.Connection, scope: Scope) -> list[dict]:
    """One row per scoped match: hero_id, won, and each LANING_METRICS value at
    the lane-end snapshot. The win-condition twin of personal_laning -- same
    joins/predicates, plus mp.won so the service can split a match into met /
    not-met by a laning condition and read off each side's win rate."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    select = ", ".join(f"({mdef['expr']}) AS {mdef['key']}" for mdef in LANING_METRICS)
    sql = (
        f"SELECT mp.hero_id AS hero_id, mp.won AS won, {select}"
        " FROM laning_stats ls"
        " JOIN match_players mp ON mp.match_id = ls.match_id"
        "   AND mp.player_slot = ls.player_slot"
        " JOIN matches m ON m.match_id = ls.match_id"
        " WHERE mp.account_id = ? AND m.game_mode = ? AND m.duration_s > 0"
        + era_sql + badge_sql
    )
    params = [scope.account_id, scope.game_mode] + era_params + badge_params
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def baseline_laning(conn: sqlite3.Connection, scope: Scope,
                    my_hero_ids: list[int]) -> dict:
    """Population mean of each laning metric at the lane-end mark, computed live
    from laning_stats -- the laning analogue of baseline_performance. There is no
    stored continuous baseline; every player's lane-end snapshot is materialized,
    so "the typical player at this scope" is an AVG over the same era/badge/
    game-mode predicates, with the scoped account excluded ("you vs the field").
    Each population row is badge-scoped by its OWN team average, like the personal
    side. Returns {hero_id: {"n": games, <metric_key>: mean_or_None, ...}} plus an
    "overall" entry pooled across exactly the heroes you played (my_hero_ids)."""
    era_sql, era_params = _era_clause(scope, "m.era_id")
    badge_sql, badge_params = _badge_clause(scope, "mp.team")
    avg_select = ", ".join(f"AVG({mdef['expr']}) AS {mdef['key']}" for mdef in LANING_METRICS)
    base_from = (
        " FROM laning_stats ls"
        " JOIN match_players mp ON mp.match_id = ls.match_id"
        "   AND mp.player_slot = ls.player_slot"
        " JOIN matches m ON m.match_id = ls.match_id"
        " WHERE mp.account_id != ? AND m.game_mode = ? AND m.duration_s > 0"
        + era_sql + badge_sql
    )
    base_params = [scope.account_id, scope.game_mode] + era_params + badge_params

    out: dict = {}
    per_hero_sql = (f"SELECT mp.hero_id AS hero_id, COUNT(*) AS n, {avg_select}"
                    + base_from + " GROUP BY mp.hero_id")
    for r in conn.execute(per_hero_sql, base_params).fetchall():
        out[r["hero_id"]] = {k: r[k] for k in r.keys() if k != "hero_id"}

    if my_hero_ids:
        hero_ph = ",".join("?" for _ in my_hero_ids)
        overall_sql = (f"SELECT COUNT(*) AS n, {avg_select}"
                       + base_from + f" AND mp.hero_id IN ({hero_ph})")
        row = conn.execute(overall_sql, base_params + list(my_hero_ids)).fetchone()
        if row is not None:
            out["overall"] = {k: row[k] for k in row.keys()}
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

# Manual labels are keyed by user_id (account_labels) and are PRIVATE to that user.
# resolve_names() defaults to DEFAULT_USER_ID (the local/dev user) so the CLI and
# local mode read their own labels, but every request-scoped caller must thread the
# viewer's own user_id -- and pass None for an anonymous viewer (auth on, nobody
# logged in), which skips labels entirely so one user's names never leak to another.


def _labels_for(conn: sqlite3.Connection, account_ids: list[int],
                user_id: int) -> dict[int, str]:
    """{account_id: display_name} from account_labels for one user, restricted to
    the requested ids."""
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    rows = conn.execute(
        f"SELECT account_id, display_name FROM account_labels"
        f" WHERE user_id = ? AND account_id IN ({placeholders})",
        [user_id, *account_ids],
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


def resolve_names(conn: sqlite3.Connection, account_ids,
                  user_id: int | None = DEFAULT_USER_ID) -> dict[int, str]:
    """{account_id: name} for every requested id, with precedence: this user's
    manual label > steam_personas.persona_name > str(account_id). Every id resolves
    to a string (never None), so callers can surface co-players and opponents --
    mostly untracked -- by their best name.

    user_id=None means an anonymous viewer (auth on, nobody logged in): skip the
    private labels entirely and resolve from Steam personas / bare ids only, so one
    user's labels are never shown to another viewer."""
    ids = list(dict.fromkeys(account_ids))   # dedupe, preserve order
    labels = _labels_for(conn, ids, user_id) if user_id is not None else {}
    personas = _persona_names(conn, ids)
    return {
        aid: (labels.get(aid) or personas.get(aid) or str(aid))
        for aid in ids
    }


def item_images(conn: sqlite3.Connection) -> dict[int, str | None]:
    """item_id -> shop-art URL (items.image_url, from the assets loader).
    May be None for items whose asset row has no image."""
    return {r["item_id"]: r["image_url"] for r in
            conn.execute("SELECT item_id, image_url FROM items").fetchall()}


def ability_assets(conn: sqlite3.Connection) -> dict[int, dict]:
    """ability_id -> {name, ability_type, image_url} from the abilities reference
    table (loaded by tracker.reference.load_abilities). Abilities live nowhere in
    the items table, so this is the only name/icon source for a skill-up. name/type/
    image may be None for a sparse asset row -- callers fall back to str(id)."""
    return {r["ability_id"]: {"name": r["name"], "ability_type": r["ability_type"],
                              "image_url": r["image_url"]}
            for r in conn.execute(
                "SELECT ability_id, name, ability_type, image_url FROM abilities"
            ).fetchall()}


def list_ranks(conn: sqlite3.Connection) -> list[dict]:
    """Rank tiers ordered low to high (name + color; art derived in service)."""
    return [dict(r) for r in
            conn.execute("SELECT tier, name, color FROM ranks ORDER BY tier").fetchall()]


# ── Overview / sync / eras ───────────────────────────────────────────────────

def mmr_series(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    """The full rank-over-time series for an account, ordered by the rank rows'
    OWN timestamp (account_rank_history.recorded_at, from the mmr-history
    endpoint). Deliberately no join to matches: the old INNER JOIN dropped every
    rank point whose match we never ingested (api-findings, spike 12)."""
    rows = conn.execute(
        "SELECT match_id, badge, recorded_at AS start_time"
        " FROM account_rank_history"
        " WHERE account_id = ? AND recorded_at IS NOT NULL"
        " ORDER BY recorded_at",
        (account_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def last_matches(conn: sqlite3.Connection, account_id: int, limit: int = 10) -> list[dict]:
    """The account's most recent matches for the Overview card. Reads
    v_account_matches so a freshly imported account sees its latest games at once
    (from provisional summaries), each row carrying `source` so the UI can badge a
    summary-backed row as provisional and upgrade it in place once metadata lands.
    No game_mode filter: Overview shows the raw recent history across modes."""
    rows = conn.execute(
        "SELECT mp.match_id, mp.hero_id, mp.won, mp.kills, mp.deaths, mp.assists,"
        " mp.net_worth, mp.start_time, mp.game_mode, mp.source"
        " FROM v_account_matches mp"
        " WHERE mp.account_id = ?"
        " ORDER BY mp.start_time DESC, mp.match_id DESC"
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


def match_ability_events(conn: sqlite3.Connection, match_id: int,
                         player_slot: int) -> list[dict]:
    """One player's ability points in a match, in skill-up order (point_number).
    Keyed on player_slot like match_purchases, so an anonymized account_id = 0
    row is still isolated to one player. ability_id joins the abilities reference
    table for name/icon at the service layer."""
    rows = conn.execute(
        "SELECT ability_id, point_number, game_time_s"
        " FROM ability_events WHERE match_id = ? AND player_slot = ?"
        " ORDER BY point_number",
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


def account_progress_counts(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """The onboarding-progress counts for one account, for the polled progress
    endpoint. Every query is a single indexed COUNT -- cheap enough to poll:

    - known:    summary rows we hold (discovery materialized them). idx_ams_account_start.
    - analyzed: matches with full metadata ingested. idx_mp_account_match -- this
                is the view's source='full' count for the account, read without
                paying for the UNION.
    - prioritized_pending / backfill_pending / deferred: this account's remaining
      queue depth by tier, keyed on discovered_for_account (the first discoverer;
      schema v18). All hit the idx_fetch_queue_account(status, discovered_for_account)
      prefix. 'pending' is split by priority so the fresh, user-facing tier
      (priority > 0) is distinguished from any legacy priority-0 pending rows."""
    known = conn.execute(
        "SELECT COUNT(*) AS n FROM account_match_summaries WHERE account_id = ?",
        (account_id,),
    ).fetchone()["n"]
    analyzed = conn.execute(
        "SELECT COUNT(*) AS n FROM match_players WHERE account_id = ?",
        (account_id,),
    ).fetchone()["n"]
    prioritized_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM fetch_queue"
        " WHERE discovered_for_account = ? AND status = 'pending' AND priority > 0",
        (account_id,),
    ).fetchone()["n"]
    backfill_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM fetch_queue"
        " WHERE discovered_for_account = ? AND status = 'backfill'",
        (account_id,),
    ).fetchone()["n"]
    deferred = conn.execute(
        "SELECT COUNT(*) AS n FROM fetch_queue"
        " WHERE discovered_for_account = ? AND status = 'deferred'",
        (account_id,),
    ).fetchone()["n"]
    return {
        "known": known, "analyzed": analyzed,
        "prioritized_pending": prioritized_pending,
        "backfill_pending": backfill_pending, "deferred": deferred,
    }


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
