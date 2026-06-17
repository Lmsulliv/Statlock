"""Read-only diagnostic for the sparse "recurring players" screen.

Why this exists: the recurring-players feature returns far fewer co-players than
expected. This script doesn't fix anything -- it gathers the evidence needed to
decide WHICH of four hypotheses is responsible:

  - sparse-by-design  : I genuinely don't share many matches with repeat players
  - anonymized ids    : co-players' account_ids are sentinels (0 / NULL), so the
                        self-join can't tell two different strangers apart
  - game_mode filter  : the production `m.game_mode = "1"` predicate is throwing
                        away most matches (e.g. they're stored under a different
                        mode, or NULL)
  - partial rosters   : matches were ingested with only my own row, not all 12
                        players, so there's no one to co-occur with

It opens the SAME database the app reads (api.config.db_path) through the SAME
connection helper (tracker.db.connect) and issues SELECTs only -- no writes, no
schema changes, no API calls.
"""
import sys
from pathlib import Path

# scripts/ lives one level below the project root; put the root on the path so
# `import api...` / `import tracker...` resolve exactly like the app's do.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.config import db_path
from api.scope import GAME_MODE_NORMAL
from tracker.db import connect


def header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("-" * 72)


def fraction(part: int, whole: int) -> str:
    """Human-readable 'n/total (xx.x%)', guarding against divide-by-zero."""
    pct = (100.0 * part / whole) if whole else 0.0
    return f"{part}/{whole} ({pct:.1f}%)"


# ── The self-join under test ────────────────────────────────────────────────
# A faithful copy of api.queries.recurring_co_players' core, with the game_mode
# predicate made optional so we can run it both ways and measure the gap.
def co_occurrence(conn, self_account_id: int, *, game_mode_filter: bool):
    mode_sql = " AND m.game_mode = ?" if game_mode_filter else ""
    sql = (
        "SELECT other.account_id AS account_id,"
        " (other.team = me.team) AS same_team,"
        " COUNT(*) AS games"
        " FROM match_players me"
        " JOIN match_players other"
        "   ON other.match_id = me.match_id AND other.account_id != me.account_id"
        " JOIN matches m ON m.match_id = me.match_id"
        " WHERE me.account_id = ?" + mode_sql +
        " GROUP BY other.account_id, same_team"
        " ORDER BY games DESC"
    )
    params = [self_account_id]
    if game_mode_filter:
        params.append(GAME_MODE_NORMAL)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def report_co_occurrence(label: str, rows: list[dict]) -> int:
    """Print the top-20 grouped rows and return how many reach >= 3 games."""
    at_or_above_3 = sum(1 for r in rows if r["games"] >= 3)
    print(f"  [{label}]")
    print(f"    distinct (account_id, same_team) groups : {len(rows)}")
    print(f"    groups with games >= 3 (the recurring floor): {at_or_above_3}")
    print(f"    top 20 by games desc:")
    if not rows:
        print("      (none)")
    for r in rows[:20]:
        side = "teammate" if r["same_team"] else "opponent"
        print(f"      account_id={r['account_id']:<12} games={r['games']:<4} {side}")
    return at_or_above_3


def main() -> None:
    path = db_path()
    print(f"DB path (api.config.db_path): {path}")
    if not Path(path).exists():
        print("!! database file does not exist; nothing to diagnose.")
        return

    conn = connect(path)

    # ── 1. Match volume & game_mode distribution ───────────────────────────
    header("1. Matches total + game_mode distribution")
    total_matches = conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
    print(f"  total matches: {total_matches}")
    mode_rows = conn.execute(
        "SELECT game_mode, COUNT(*) AS c FROM matches GROUP BY game_mode ORDER BY c DESC"
    ).fetchall()
    normal_count = 0
    for r in mode_rows:
        gm = r["game_mode"]
        shown = "NULL" if gm is None else repr(gm)
        print(f"    game_mode={shown:<8} count={r['c']}  ({fraction(r['c'], total_matches)})")
        if gm == GAME_MODE_NORMAL:
            normal_count = r["c"]
    if total_matches:
        if normal_count == 0:
            print('  >> FLAG: no matches have game_mode == "1"; the production filter '
                  "drops everything.")
        elif normal_count < total_matches:
            print(f'  >> NOTE: only {fraction(normal_count, total_matches)} of matches are '
                  'mode "1"; the rest are excluded by the production filter.')
        else:
            print('  >> game_mode is uniformly "1"; the mode filter excludes nothing.')

    # ── 2. Roster completeness ─────────────────────────────────────────────
    header("2. Roster completeness (players-per-match distribution)")
    roster_rows = conn.execute(
        "SELECT c AS players, COUNT(*) AS matches FROM ("
        "  SELECT match_id, COUNT(*) AS c FROM match_players GROUP BY match_id"
        ") GROUP BY c ORDER BY c"
    ).fetchall()
    if not roster_rows:
        print("  (no rows in match_players)")
    spike_at_1 = 0
    spike_at_12 = 0
    for r in roster_rows:
        print(f"    players_per_match={r['players']:<3} -> {r['matches']} match(es)")
        if r["players"] == 1:
            spike_at_1 = r["matches"]
        if r["players"] == 12:
            spike_at_12 = r["matches"]
    if total_matches:
        print(f"  >> full 12-player rosters: {fraction(spike_at_12, total_matches)};"
              f"  lone-row (just me) matches: {fraction(spike_at_1, total_matches)}")
        if spike_at_1 and spike_at_1 >= spike_at_12:
            print("  >> FLAG: lone-row matches dominate -- rosters look partial "
                  "(only my own player_slot was ingested).")

    # ── 3. account_id anonymization ────────────────────────────────────────
    header("3. account_id sentinel / anonymization check")
    total_mp = conn.execute("SELECT COUNT(*) AS c FROM match_players").fetchone()["c"]
    zero_ids = conn.execute(
        "SELECT COUNT(*) AS c FROM match_players WHERE account_id = 0"
    ).fetchone()["c"]
    null_ids = conn.execute(
        "SELECT COUNT(*) AS c FROM match_players WHERE account_id IS NULL"
    ).fetchone()["c"]
    print(f"  total match_players rows: {total_mp}")
    print(f"    account_id = 0   : {fraction(zero_ids, total_mp)}")
    print(f"    account_id IS NULL: {fraction(null_ids, total_mp)}")
    print("  5 most common account_ids (a sentinel would dominate this list):")
    common = conn.execute(
        "SELECT account_id, COUNT(*) AS c FROM match_players"
        " GROUP BY account_id ORDER BY c DESC LIMIT 5"
    ).fetchall()
    for r in common:
        aid = "NULL" if r["account_id"] is None else r["account_id"]
        print(f"      account_id={str(aid):<12} rows={r['c']}  ({fraction(r['c'], total_mp)})")
    sentinel_share = zero_ids + null_ids
    if total_mp and sentinel_share > total_mp * 0.5:
        print(f"  >> FLAG: {fraction(sentinel_share, total_mp)} of co-player rows are "
              "sentinels; distinct strangers collapse into one bucket.")

    # ── 4. The self account ────────────────────────────────────────────────
    header("4. tracked_accounts (expect exactly one is_self = 1)")
    accounts = conn.execute(
        "SELECT account_id, display_name, is_self FROM tracked_accounts"
        " ORDER BY is_self DESC, account_id"
    ).fetchall()
    self_ids = []
    for r in accounts:
        marker = "  <-- self" if r["is_self"] else ""
        print(f"    account_id={r['account_id']:<12} is_self={r['is_self']} "
              f"name={r['display_name']!r}{marker}")
        if r["is_self"]:
            self_ids.append(r["account_id"])
    if len(self_ids) == 1:
        print(f"  >> exactly one is_self account: {self_ids[0]} (good).")
    elif not self_ids:
        print("  >> FLAG: no is_self account is set; the recurring screen has no anchor.")
    else:
        print(f"  >> FLAG: {len(self_ids)} accounts are flagged is_self; should be 1.")

    # ── 5. Co-occurrence histogram, with vs without the game_mode filter ───
    header("5. Co-occurrence for the self account: production filter vs none")
    if not self_ids:
        print("  (no self account resolved; skipping)")
        conn.close()
        return
    self_account_id = self_ids[0]
    print(f"  self account_id: {self_account_id}")
    print()
    with_filter = co_occurrence(conn, self_account_id, game_mode_filter=True)
    above_with = report_co_occurrence('WITH m.game_mode = "1" (production)', with_filter)
    print()
    without_filter = co_occurrence(conn, self_account_id, game_mode_filter=False)
    above_without = report_co_occurrence("WITHOUT any game_mode filter", without_filter)
    print()
    print(f"  >> recurring co-players (>=3 games): {above_with} with filter vs "
          f"{above_without} without.")

    # ── Plain-English verdict ──────────────────────────────────────────────
    header("SUMMARY (interpretation -- not a fix)")
    lines = []
    # game_mode hypothesis
    if total_matches and normal_count < total_matches:
        gap = above_without - above_with
        if gap > 0:
            lines.append(
                f'- game_mode filter LOOKS GUILTY: dropping the mode="1" predicate '
                f"raises recurring co-players from {above_with} to {above_without} "
                f"(+{gap}); many matches aren't stored under mode \"1\".")
        else:
            lines.append(
                f'- game_mode: matches are mixed-mode ({fraction(normal_count, total_matches)} '
                f'are "1") but removing the filter doesn\'t add recurring players, so '
                "the mode predicate isn't the main culprit.")
    else:
        lines.append('- game_mode: ruled OUT -- essentially all matches are mode "1", '
                     "so the production filter excludes nothing here.")
    # partial-roster hypothesis
    if total_matches and spike_at_1 and spike_at_1 >= spike_at_12:
        lines.append(
            f"- partial rosters LOOK GUILTY: {fraction(spike_at_1, total_matches)} of "
            "matches contain only my own row, so there are no co-players to count.")
    else:
        lines.append(
            f"- partial rosters: mostly OK -- {fraction(spike_at_12, total_matches)} of "
            "matches carry a full 12-player roster.")
    # anonymized-ids hypothesis
    if total_mp and (zero_ids + null_ids) > total_mp * 0.5:
        lines.append(
            f"- anonymized ids LOOK GUILTY: {fraction(zero_ids + null_ids, total_mp)} of "
            "co-player rows are 0/NULL sentinels, collapsing distinct strangers together.")
    else:
        lines.append(
            f"- anonymized ids: ruled OUT -- only {fraction(zero_ids + null_ids, total_mp)} "
            "of co-player rows are sentinels, so real account_ids are present.")
    # sparse-by-design fallback
    lines.append(
        "- if the three above are all ruled out, the data is simply sparse-by-design: "
        f"with {total_matches} matches you just don't share >=3 games with many repeat "
        "players yet.")
    for ln in lines:
        print(ln)

    conn.close()


if __name__ == "__main__":
    main()
