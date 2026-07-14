"""Skill-order aggregation: what abilities you level, and in what order.

Pure functions, no DB/HTTP -- the storage-facing reader passes in per-game skill
sequences (from ability_events) and this module tallies them. Everything here is
DESCRIPTIVE: there is no population baseline for skill order, so we report modal
sequences with raw game counts and never a Wilson verdict (hard rule 1 keeps the
stats here; there just is no significance math to run). The wins-vs-losses split
is gated behind stats.VERDICT_FLOOR on EACH side, so a "your order in wins" claim
never rests on a couple of games.

Definitions (docs/api-findings.md, "Ability level-up extraction rule"):
- a game's skill order is its ability_ids ordered by point_number;
- the opening sequence is the first OPENING_POINTS (4) of that order;
- an ability is "maxed" at its MAX_LEVEL-th (4th) point; the first-maxed ability
  is the one that reaches its 4th point earliest (lowest point_number).
"""
from collections import Counter
from dataclasses import dataclass

from stats import VERDICT_FLOOR

OPENING_POINTS = 4   # "first four points" -- the opening skill order
MAX_LEVEL = 4        # an ability caps at 4 points (unlock + 3 upgrades)


@dataclass(frozen=True)
class Game:
    """One game's skill-up sequence for the scoped account on one hero.

    `points` is the ability_ids in skill-up order (by point_number). `won` splits
    the wins/losses view."""
    won: bool
    points: tuple[int, ...]


def _modal_opening(games: list[Game]) -> dict | None:
    """The most common opening sequence (first OPENING_POINTS ability_ids) and how
    many of the considered games used it. Only games with at least OPENING_POINTS
    points are considered (a shorter game can't have a full opening). None when no
    game qualifies."""
    openings = [g.points[:OPENING_POINTS] for g in games
                if len(g.points) >= OPENING_POINTS]
    if not openings:
        return None
    sequence, count = Counter(openings).most_common(1)[0]
    return {"sequence": list(sequence), "games": count, "considered": len(openings)}


def _first_maxed_ability(points: tuple[int, ...]) -> int | None:
    """The ability_id that reaches MAX_LEVEL points first in this game's order, or
    None if no ability was maxed (a short game)."""
    counts: Counter[int] = Counter()
    for ability_id in points:
        counts[ability_id] += 1
        if counts[ability_id] == MAX_LEVEL:
            return ability_id
    return None


def _modal_first_maxed(games: list[Game]) -> dict | None:
    """The ability most often maxed first, and how many of the considered games did
    so. Only games where some ability reached MAX_LEVEL are considered. None when
    none qualify."""
    firsts = [a for a in (_first_maxed_ability(g.points) for g in games)
              if a is not None]
    if not firsts:
        return None
    ability_id, count = Counter(firsts).most_common(1)[0]
    return {"ability_id": ability_id, "games": count, "considered": len(firsts)}


def _facts(games: list[Game]) -> dict:
    """The two descriptive facts (opening + first-maxed) over a set of games."""
    return {
        "games": len(games),
        "opening": _modal_opening(games),
        "first_maxed": _modal_first_maxed(games),
    }


def summarize(games: list[Game], *, floor: int = VERDICT_FLOOR) -> dict:
    """Descriptive skill-order summary for one hero.

    Returns the overall opening sequence + first-maxed ability (over all games),
    plus a wins/losses `split` ONLY when EACH side has at least `floor` games -- so
    the split is shown exactly when both halves carry enough evidence to be worth
    reading, matching the honesty floor the self-baseline screens use. ability_ids
    stay raw here; the service layer decorates them with names/icons."""
    result = _facts(games)
    result["split"] = None
    wins = [g for g in games if g.won]
    losses = [g for g in games if not g.won]
    if len(wins) >= floor and len(losses) >= floor:
        result["split"] = {"wins": _facts(wins), "losses": _facts(losses)}
    return result
