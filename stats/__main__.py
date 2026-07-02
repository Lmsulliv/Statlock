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
from stats.trends import TRENDS_WINDOW_DEFAULT
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


def _num(x: float | None) -> str:
    return "" if x is None else f"{x:.2f}"


def _metric_scope_label(screen: str, scope: Scope) -> str:
    """Presentation rule 4: a stat is always printed next to its scope. Shared by
    the continuous-metric screens (Performance, Laning), which differ only in name."""
    mode = _MODE_LABEL.get(scope.game_mode, f"mode {scope.game_mode}")
    era = "all eras" if scope.era_ids is None else "eras " + ",".join(map(str, scope.era_ids))
    badge = "all ranks" if scope.is_full_badge_range else f"badge {scope.badge_min}-{scope.badge_max}"
    return f"{screen} - {mode}, {era}, {badge}"


def _render_metric_blocks(rows: list[dict], scope: Scope, *, screen: str,
                          empty_noun: str) -> str:
    """Pure: rows + scope -> printable table, one block per hero (overall first).
    Deterministic, so the test reproduces it from the same service rows (the
    API<->CLI regression that proves both callers share one code path). Shared by
    Performance and Laning, which have the same row shape (a metrics list)."""
    lines = [_metric_scope_label(screen, scope)]
    if not rows:
        lines.append("")
        lines.append(f"No {empty_noun} meets this scope yet. Ingest more matches"
                     " or widen the scope.")
        return "\n".join(lines)

    for row in rows:
        title = "Overall" if row["scope"] == "overall" else row["hero_name"]
        lines.append("")
        lines.append(f"== {title} ({row['games']} games) ==")
        lines.append(f"{'Metric':<22}{'n':>4}{'Mean':>12}{'95% CI':>22}"
                     f"{'Baseline':>12}{'Delta':>10}  Verdict")
        for m in row["metrics"]:
            ci = (f"{_num(m['ci_low'])}-{_num(m['ci_high'])}"
                  if m["mean"] is not None else "")
            delta = "" if m["delta"] is None else f"{m['delta']:+.2f}"
            lines.append(
                f"{m['label'][:21]:<22}{m['games']:>4}{_num(m['mean']):>12}"
                f"{ci:>22}{_num(m['baseline_mean']):>12}{delta:>10}"
                f"  {_VERDICT_LABEL[m['verdict']]}"
            )
    return "\n".join(lines)


def render_performance(rows: list[dict], scope: Scope) -> str:
    return _render_metric_blocks(rows, scope, screen="Performance",
                                 empty_noun="performance data")


def render_laning(rows: list[dict], scope: Scope) -> str:
    return _render_metric_blocks(rows, scope, screen="Laning",
                                 empty_noun="laning data")


def _trends_scope_label(scope: Scope, result: dict) -> str:
    """Presentation rule 4: a stat is always printed next to its scope (here also
    the trend shape -- rolling window width or calendar granularity)."""
    mode = _MODE_LABEL.get(scope.game_mode, f"mode {scope.game_mode}")
    era = "all eras" if scope.era_ids is None else "eras " + ",".join(map(str, scope.era_ids))
    badge = "all ranks" if scope.is_full_badge_range else f"badge {scope.badge_min}-{scope.badge_max}"
    if result["mode"] == "calendar":
        shape = f"calendar/{result['granularity']}"
    else:
        shape = f"rolling/{result['window_games']}"
    return f"Trends ({shape}) - {mode}, {era}, {badge}"


def render_trends(result: dict, scope: Scope) -> str:
    """Pure: a trends result + scope -> printable tables, one block per metric
    (win rate first). Deterministic, so the test reproduces it from the same
    service result (the API<->CLI regression that proves one shared code path)."""
    lines = [_trends_scope_label(scope, result)]
    metrics = result["metrics"]
    if not metrics:
        lines.append("")
        lines.append("No trend data meets this scope yet. Ingest more matches"
                     " or widen the scope.")
        return "\n".join(lines)

    for m in metrics:
        is_rate = m["key"] == "win_rate"
        fmt = _pct if is_rate else _num
        base = m["baseline"]
        base_str = "no baseline" if base is None else fmt(base)
        lines.append("")
        lines.append(f"== {m['label']} (baseline {base_str}) ==")
        lines.append(f"{'Bucket':<14}{'n':>4}{'Value':>10}")
        for p in m["points"]:
            flag = "" if p["enough_data"] else "  (thin)"
            lines.append(f"{p['label'][:13]:<14}{p['n']:>4}{fmt(p['value']):>10}{flag}")
    return "\n".join(lines)


def render_deaths(result: dict, scope: Scope) -> str:
    """Pure: a death-patterns result + scope -> printable tables (the by-enemy-hero
    ranking, then the timing distribution). Deterministic, so the test reproduces
    it from the same service result (the API<->CLI regression). The by-hero block
    is raw counts with no verdict; the timing block mirrors the metric tables."""
    mode = _MODE_LABEL.get(scope.game_mode, f"mode {scope.game_mode}")
    era = "all eras" if scope.era_ids is None else "eras " + ",".join(map(str, scope.era_ids))
    badge = "all ranks" if scope.is_full_badge_range else f"badge {scope.badge_min}-{scope.badge_max}"
    lines = [f"Deaths - {mode}, {era}, {badge} ({result['games']} games)"]

    by_hero = result["by_enemy_hero"]
    by_damage = result.get("by_damage_source", [])
    timeline = result["timeline"]
    if not by_hero and not timeline:
        lines.append("")
        lines.append("No deaths data meets this scope yet. Ingest more matches"
                     " or widen the scope.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Deaths by enemy hero (raw counts, no verdict):")
    lines.append(f"{'Enemy':<16}{'Faced':>7}{'Deaths':>8}")
    for r in by_hero:
        lines.append(f"{r['enemy_hero_name'][:15]:<16}{r['games_faced']:>7}{r['deaths']:>8}")

    lines.append("")
    lines.append("Damage taken by enemy hero (gross avg/game, no verdict):")
    lines.append(f"{'Enemy':<16}{'Faced':>7}{'Avg/game':>12}")
    for r in by_damage:
        lines.append(f"{r['enemy_hero_name'][:15]:<16}{r['games_faced']:>7}"
                     f"{_num(r['avg_per_game']):>12}")

    lines.append("")
    lines.append("Death timing vs the field (deaths per game by game-minute):")
    lines.append(f"{'Bin':<10}{'Deaths':>7}{'Mean':>9}{'95% CI':>18}"
                 f"{'Baseline':>10}{'Delta':>9}  Verdict")
    for b in timeline:
        ci = f"{_num(b['ci_low'])}-{_num(b['ci_high'])}" if b["mean"] is not None else ""
        delta = "" if b["delta"] is None else f"{b['delta']:+.2f}"
        lines.append(
            f"{b['label']:<10}{b['deaths']:>7}{_num(b['mean']):>9}{ci:>18}"
            f"{_num(b['baseline_mean']):>10}{delta:>9}  {_VERDICT_LABEL[b['verdict']]}"
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


def _add_scope_args(p: argparse.ArgumentParser) -> None:
    """The standard scope flags every subcommand shares (the CLI mirror of the
    API's scope query params)."""
    p.add_argument("--db", default=None, help="SQLite path (default data/tracker.db)")
    p.add_argument("--account", type=int, default=None, help="account id (default: self)")
    p.add_argument("--era", default=None, help="comma-separated era ids, or 'all'")
    p.add_argument("--badge-min", type=int, default=FULL_BADGE_MIN)
    p.add_argument("--badge-max", type=int, default=FULL_BADGE_MAX)
    p.add_argument("--min-games", type=int, default=DEFAULT_MIN_GAMES)
    p.add_argument("--game-mode", default=GAME_MODE_NORMAL,
                   help="'1' Normal (default), '4' Street Brawl")


def _scope_from_args(args) -> Scope:
    return make_scope(
        account_id=args.account, era_ids=args.era,
        badge_min=args.badge_min, badge_max=args.badge_max,
        min_games=args.min_games, game_mode=args.game_mode,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m stats", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("matchups", help="print the matchup table for a scope")
    m.add_argument("--hero", type=int, default=None, help="filter to a hero you play")
    _add_scope_args(m)

    p = sub.add_parser("performance",
                       help="print continuous-metric performance for a scope")
    _add_scope_args(p)

    lan = sub.add_parser("laning",
                         help="print early-game (laning) metrics for a scope")
    _add_scope_args(lan)

    t = sub.add_parser("trends", help="print performance-over-time for a scope")
    t.add_argument("--mode", choices=["rolling", "calendar"], default="rolling",
                   help="rolling moving average (default) or calendar buckets")
    t.add_argument("--granularity", choices=["week", "month"], default="week",
                   help="calendar bucket size (mode=calendar)")
    t.add_argument("--window-games", type=int, default=TRENDS_WINDOW_DEFAULT,
                   help="rolling window width in games (mode=rolling)")
    t.add_argument("--hero", type=int, default=None, help="filter to a hero you play")
    _add_scope_args(t)

    d = sub.add_parser("deaths",
                       help="print death patterns (by enemy hero + timing) for a scope")
    _add_scope_args(d)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    conn = _open_db(args.db or str(db_path()))
    try:
        if args.command == "matchups":
            scope = _scope_from_args(args)
            rows = service.matchups(conn, scope, hero_id=args.hero)
            print(render_matchups(rows, scope, args.hero))
        elif args.command == "performance":
            scope = _scope_from_args(args)
            rows = service.performance(conn, scope)
            print(render_performance(rows, scope))
        elif args.command == "laning":
            scope = _scope_from_args(args)
            rows = service.laning(conn, scope)
            print(render_laning(rows, scope))
        elif args.command == "trends":
            scope = _scope_from_args(args)
            result = service.trends(conn, scope, mode=args.mode,
                                    granularity=args.granularity,
                                    window_games=args.window_games, hero_id=args.hero)
            print(render_trends(result, scope))
        elif args.command == "deaths":
            scope = _scope_from_args(args)
            result = service.death_patterns(conn, scope)
            print(render_deaths(result, scope))
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
