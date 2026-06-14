"""Pure statistics functions: Wilson interval, Bayesian shrinkage, verdicts.

All statistics math for the app lives here (CLAUDE.md hard rule 1) and is
imported everywhere else. No database, no network, stdlib math only.
Formulas follow docs/data-model.md, "Statistics layer".
"""
import math

# Confidence-aware verdict vocabulary (five tiers). A row is only "clear" when
# the 95% Wilson interval excludes the global rate; "leaning" is a softer signal
# (a looser 80% band excludes it AND the shrinkage estimate agrees on the
# direction); everything else, including anything below the sample-size floor,
# is "not_enough_data".
VERDICT_CLEAR_STRENGTH = "clear_strength"
VERDICT_LEANING_STRENGTH = "leaning_strength"
VERDICT_NOT_ENOUGH_DATA = "not_enough_data"
VERDICT_LEANING_WEAKNESS = "leaning_weakness"
VERDICT_CLEAR_WEAKNESS = "clear_weakness"

# Below this many games a matchup never earns a verdict, however lopsided the
# record (presentation-spec honesty contract; acceptance scenario 1).
VERDICT_FLOOR = 5

Z_CLEAR = 1.96    # 95% band -> a "clear" call
Z_LEAN = 1.2816   # 80% band -> a "leaning" call


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% (by default) Wilson score interval for a win rate of wins out of n.

    Returns (low, high). With no games there is no information, so the
    interval is the whole of [0, 1].
    """
    if n == 0:
        return (0.0, 1.0)
    p_hat = wins / n
    z2 = z * z
    denominator = 1 + z2 / n
    center = (p_hat + z2 / (2 * n)) / denominator
    halfwidth = (z / denominator) * math.sqrt(
        p_hat * (1 - p_hat) / n + z2 / (4 * n * n)
    )
    # At w = 0 the exact lower bound is 0, and at w = n the exact upper bound
    # is 1 (the formula collapses algebraically), but float rounding lands a
    # hair off in either direction; pin those bounds so callers can trust them.
    low = 0.0 if wins == 0 else max(0.0, center - halfwidth)
    high = 1.0 if wins == n else min(1.0, center + halfwidth)
    return (low, high)


def shrunk_rate(wins: int, n: int, global_rate: float, k: float = 10.0) -> float:
    """Personal win rate shrunk toward the global rate (Beta prior, strength k).

    Per the spec: alpha = k * global_rate, beta = k * (1 - global_rate),
    adjusted = (wins + alpha) / (n + alpha + beta). Since alpha + beta = k,
    that simplifies to the expression below.
    """
    return (wins + k * global_rate) / (n + k)


def verdict(wins: int, n: int, global_rate: float) -> str:
    """Confidence-aware call for a personal rate against the global rate.

    Tiers, strongest evidence first:
    - Below VERDICT_FLOOR games -> always not_enough_data (too little to say).
    - "clear" when the 95% Wilson interval excludes the global rate.
    - "leaning" when only a looser 80% band excludes it AND the shrinkage
      estimate (which pulls thin samples toward the global prior) agrees on the
      direction -- so a sample that shrinks back to the baseline stays muted.
    - otherwise not_enough_data.
    """
    if n < VERDICT_FLOOR:
        return VERDICT_NOT_ENOUGH_DATA

    low, high = wilson_interval(wins, n, Z_CLEAR)
    if low > global_rate:
        return VERDICT_CLEAR_STRENGTH
    if high < global_rate:
        return VERDICT_CLEAR_WEAKNESS

    lean_low, lean_high = wilson_interval(wins, n, Z_LEAN)
    adjusted = shrunk_rate(wins, n, global_rate)
    if lean_low > global_rate and adjusted > global_rate:
        return VERDICT_LEANING_STRENGTH
    if lean_high < global_rate and adjusted < global_rate:
        return VERDICT_LEANING_WEAKNESS
    return VERDICT_NOT_ENOUGH_DATA
