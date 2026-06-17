"""The scope selector, server-side.

Every analytical query carries the same scope (presentation-spec "scope
selector"): which account, which eras, which rank range, the minimum games to
show a row, and which game mode. Encoding it in one immutable object means the
API and the CLI parse identical inputs into identical queries, so a bookmarked
URL renders the same numbers anywhere (acceptance scenario 4).
"""
from dataclasses import dataclass

# Full numeric badge range (api-findings: badge = tier*10 + subtier, 0..116).
FULL_BADGE_MIN = 0
FULL_BADGE_MAX = 116

# game_mode is stored as TEXT on matches (ingest/parse.py). "1" = Normal,
# "4" = Street Brawl (docs/api-findings.md). Matchup/item analysis is only
# meaningful for the standard mode, so Normal is the default and Brawl never
# mixes in unless explicitly requested.
GAME_MODE_NORMAL = "1"

DEFAULT_MIN_GAMES = 3


@dataclass(frozen=True)
class Scope:
    account_id: int | None = None          # None -> resolve to the is_self account
    era_ids: tuple[int, ...] | None = None  # None -> all-time (no era filter)
    badge_min: int = FULL_BADGE_MIN
    badge_max: int = FULL_BADGE_MAX
    min_games: int = DEFAULT_MIN_GAMES
    game_mode: str = GAME_MODE_NORMAL
    in_lane: bool = False                   # True -> restrict to your lane pair

    @property
    def is_full_badge_range(self) -> bool:
        """Whole ladder selected. When true, the badge predicate is dropped so
        matches with an unknown (NULL) average badge are still counted; a
        narrower range honestly excludes them (we can't claim their bracket)."""
        return self.badge_min <= FULL_BADGE_MIN and self.badge_max >= FULL_BADGE_MAX


def parse_era_ids(raw: str | None) -> tuple[int, ...] | None:
    """Parse the era_ids query param. Absent or "all" -> None (all-time).
    Otherwise a comma-separated list of integer era ids."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "" or raw.lower() == "all":
        return None
    return tuple(int(part) for part in raw.split(",") if part.strip() != "")


def snap_badge_range(badge_min: int, badge_max: int) -> tuple[int, int]:
    """Snap a requested badge range outward to decade edges so the baseline
    containment predicate (api/queries.py) never splits a decade bracket. The
    brackets are [0,9],[10,19],…,[110,116] (ingest.maintenance.DECADE_BRACKETS);
    rank filtering is therefore decade-granular -- the finest clean partition the
    analytics badge filter supports (api-findings finding 6). Leaves the (0,116)
    full-range sentinel intact (116 -> 119 -> clamped to 116), so
    is_full_badge_range still holds for the whole ladder."""
    return (badge_min // 10) * 10, min((badge_max // 10) * 10 + 9, FULL_BADGE_MAX)


def make_scope(
    account_id: int | None = None,
    era_ids: str | None = None,
    badge_min: int = FULL_BADGE_MIN,
    badge_max: int = FULL_BADGE_MAX,
    min_games: int = DEFAULT_MIN_GAMES,
    game_mode: str = GAME_MODE_NORMAL,
    in_lane: bool = False,
) -> Scope:
    """Build a Scope from raw request/CLI values (era_ids as a comma string).
    The badge range is snapped to decade edges (the only entry point that snaps;
    a directly-constructed Scope is taken as-is)."""
    badge_min, badge_max = snap_badge_range(badge_min, badge_max)
    return Scope(
        account_id=account_id,
        era_ids=parse_era_ids(era_ids),
        badge_min=badge_min,
        badge_max=badge_max,
        min_games=min_games,
        game_mode=game_mode,
        in_lane=in_lane,
    )
