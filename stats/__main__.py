"""CLI stepping stone: `python -m stats matchups [--hero X]`.

Prints the matchup table to the terminal using the EXACT same query + stats
code as the API (api.service.matchups), so the numbers can never drift between
the CLI and the web layer. This is the spec's "stepping stone": it makes the
statistics layer usable and testable before any frontend exists.

Importing api.service pulls in stats + queries only, not FastAPI (api/__init__
is deliberately empty), so the CLI stays lightweight.
"""
import argparse
import contextlib
import io
import sys
from pathlib import Path

from api import service
from api.config import db_path
from api.scope import (
    DEFAULT_MIN_GAMES,
    FULL_BADGE_MAX,
    FULL_BADGE_MIN,
    GAME_MODE_NORMAL,
    Scope,
    make_scope,
)
from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_LEANING_STRENGTH,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
)
from tracker.db import connect
from tracker.migrate import migrate

# ASCII only: a stats CLI shouldn't blow up on a cp1252 Windows console.
_VERDICT_LABEL = {
    VERDICT_CLEAR_STRENGTH: "STRENGTH",
    VERDICT_LEANING_STRENGTH: "strength?",
    VERDICT_NOT_ENOUGH_DATA: "-",
    VERDICT_LEANING_WEAKNESS: "weakness?",
    VERDICT_CLEAR_WEAKNESS: "WEAKNESS",
}

_MODE_LABEL = {"1": "Normal", "4": "Street Brawl"}


def _pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:.1f}%"


def _scope_label(scope: Scope, hero_id: int | None, total_games: int) -> str:
    """Presentation rule 4: a stat is always printed next to its scope."""
    mode = _MODE_LABEL.get(scope.game_mode, f"mode {scope.game_mode}")
    era = "all eras" if scope.era_ids is None else "eras " + ",".join(map(str, scope.era_ids))
    badge = "all ranks" if scope.is_full_badge_range else f"badge {scope.badge_min}-{scope.badge_max}"
    hero = f"hero {hero_id}, " if hero_id is not None else ""
    return (f"Matchups - {hero}{mode}, {era}, {badge}, min {scope.min_games} games"
            f" ({total_games} games shown)")


def render_matchups(rows: list[dict], scope: Scope, hero_id: int | None = None) -> str:
    """Pure: rows + scope -> printable table. Deterministic, so the test can
    reproduce it exactly from the same service rows (API↔CLI regression)."""
    total = sum(r["games"] for r in rows)
    lines = [_scope_label(scope, hero_id, total)]
    if not rows:
        lines.append("")
        lines.append("No matchups meet this scope yet. Ingest more matches"
                     " or lower --min-games.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"{'Enemy':<16}{'G':>4}{'W':>4}{'Win%':>8}{'95% CI':>16}"
                 f"{'Global':>9}{'Delta':>9}  Verdict")
    for r in rows:
        ci = f"{_pct(r['ci_low'])}-{_pct(r['ci_high'])}"
        delta = "" if r["delta"] is None else f"{r['delta'] * 100:+.1f}"
        lines.append(
            f"{r['enemy_hero_name'][:15]:<16}{r['games']:>4}{r['wins']:>4}"
            f"{_pct(r['winrate']):>8}{ci:>16}{_pct(r['global_rate']):>9}"
            f"{delta:>9}  {_VERDICT_LABEL[r['verdict']]}"
        )
    return "\n".join(lines)


def _open_db(path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    # Auto-migrate so a fresh path is usable, but keep migrate's progress chatter
    # off stdout -- the table is the CLI's only output.
    with contextlib.redirect_stdout(io.StringIO()):
        migrate(conn)
    return conn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m stats", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("matchups", help="print the matchup table for a scope")
    m.add_argument("--hero", type=int, default=None, help="filter to a hero you play")
    m.add_argument("--db", default=None, help="SQLite path (default data/tracker.db)")
    m.add_argument("--account", type=int, default=None, help="account id (default: self)")
    m.add_argument("--era", default=None, help="comma-separated era ids, or 'all'")
    m.add_argument("--badge-min", type=int, default=FULL_BADGE_MIN)
    m.add_argument("--badge-max", type=int, default=FULL_BADGE_MAX)
    m.add_argument("--min-games", type=int, default=DEFAULT_MIN_GAMES)
    m.add_argument("--game-mode", default=GAME_MODE_NORMAL,
                   help="'1' Normal (default), '4' Street Brawl")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    conn = _open_db(args.db or str(db_path()))
    try:
        if args.command == "matchups":
            scope = make_scope(
                account_id=args.account, era_ids=args.era,
                badge_min=args.badge_min, badge_max=args.badge_max,
                min_games=args.min_games, game_mode=args.game_mode,
            )
            rows = service.matchups(conn, scope, hero_id=args.hero)
            print(render_matchups(rows, scope, args.hero))
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
