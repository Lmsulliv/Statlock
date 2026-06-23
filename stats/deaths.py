"""Pure death-pattern shaping for the Deaths screen: bucket a player's deaths
into game-minute bins and zero-fill them across the scoped game count, ready for
the service to hand to stats.mean_interval / mean_verdict.

Like stats/trends.py and stats/sessions.py this is pure grouping (CLAUDE.md hard
rule 1): no database, no network. It only slices the plain (match_id,
game_time_s) death rows the service feeds in; the service then applies the
t-interval / verdict machinery to each bin's per-game sample. Keeping the binning
here means the personal and population sides can never disagree on where a minute
boundary falls, and the shaping can be unit-tested without a DB.
"""

# Minutes at or beyond this fold into one trailing bin. A game can run long and
# late-game deaths are sparse, so an uncapped axis trails off into many one- or
# two-death bins; folding the tail keeps the distribution legible and the
# trailing bin's sample large enough to judge. The cap lives ONLY here so the
# personal and population sides are folded identically.
DEATH_TIMELINE_CAP_MIN = 30


def minute_bin(game_time_s: int, cap: int = DEATH_TIMELINE_CAP_MIN) -> int:
    """The game-minute bin a death at game_time_s falls in: floor(seconds / 60),
    clamped to `cap` so every death at or after the cap shares one trailing bin."""
    return min(game_time_s // 60, cap)


def bin_label(minute: int, cap: int = DEATH_TIMELINE_CAP_MIN) -> str:
    """Display label for a bin: "12-13m" for a normal minute, "30m+" at the cap.
    ASCII '-' keeps the CLI console-safe (cp1252)."""
    if minute >= cap:
        return f"{cap}m+"
    return f"{minute}-{minute + 1}m"


def build_timeline(game_count: int, death_times: list[tuple[int, int]],
                   baseline_deaths_by_minute: dict[int, int], baseline_games: int,
                   cap: int = DEATH_TIMELINE_CAP_MIN) -> list[dict]:
    """Shape a player's deaths into per-minute bins, each carrying the per-game
    death-count sample the service needs for a mean / CI / verdict.

    Args:
      game_count: total scoped games -- the zero-fill denominator. A game where
        the player never died in a bin must still contribute a real 0 to that
        bin's sample, or the mean would be inflated, so every bin's sample has
        length game_count.
      death_times: the player's deaths as (match_id, game_time_s); game_time_s is
        never None here (the query drops untimed deaths, which can't be binned).
      baseline_deaths_by_minute: {uncapped_minute: population death total} for the
        same scope. Folded to capped bins here so the cap stays in one place.
      baseline_games: population player-games (the baseline denominator).
      cap: the trailing-bin minute.

    Returns one dict per bin from 0 to the last bin observed on either side
    (personal or population), so the axis covers the real distribution without
    trailing empty minutes. Each bin:
      {minute, label, deaths, per_game_counts, baseline_mean, baseline_games}
    `baseline_mean` is population deaths/games for that bin, or None when there is
    no population (baseline_games == 0), which the service reads as "no baseline".
    """
    # Personal: one count per (match, bin), then grouped into per-bin lists. A
    # match appears at most once per bin, so len(list) is the number of games
    # that had at least one death in the bin -- the rest are zero-filled below.
    per_match_bin: dict[tuple[int, int], int] = {}
    for match_id, game_time_s in death_times:
        b = minute_bin(game_time_s, cap)
        per_match_bin[(match_id, b)] = per_match_bin.get((match_id, b), 0) + 1
    counts_by_bin: dict[int, list[int]] = {}
    for (_match_id, b), n in per_match_bin.items():
        counts_by_bin.setdefault(b, []).append(n)

    # Population: fold the uncapped per-minute totals into the same capped bins.
    base_by_bin: dict[int, int] = {}
    for minute, deaths in baseline_deaths_by_minute.items():
        b = min(minute, cap)
        base_by_bin[b] = base_by_bin.get(b, 0) + deaths

    max_bin = 0
    if counts_by_bin:
        max_bin = max(max_bin, max(counts_by_bin))
    if base_by_bin:
        max_bin = max(max_bin, max(base_by_bin))

    out: list[dict] = []
    for b in range(max_bin + 1):
        nonzero = counts_by_bin.get(b, [])
        per_game_counts = nonzero + [0] * (game_count - len(nonzero))
        baseline_mean = (base_by_bin.get(b, 0) / baseline_games
                         if baseline_games else None)
        out.append({
            "minute": b,
            "label": bin_label(b, cap),
            "deaths": sum(nonzero),
            "per_game_counts": per_game_counts,
            "baseline_mean": baseline_mean,
            "baseline_games": baseline_games,
        })
    return out
