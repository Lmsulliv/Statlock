"""Pure statistics functions: Wilson interval, Bayesian shrinkage, verdicts.

All statistics math for the app lives here (CLAUDE.md hard rule 1) and is
imported everywhere else. No database, no network, stdlib math only.
Formulas follow docs/data-model.md, "Statistics layer".
"""
import math

VERDICT_STRENGTH = "strength"
VERDICT_WEAKNESS = "weakness"
VERDICT_NOT_ENOUGH_DATA = "not_enough_data"


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


def verdict(wins: int, n: int, global_rate: float, z: float = 1.96) -> str:
    """Strength/weakness call for a personal rate against the global rate.

    A row earns "strength" or "weakness" only when the Wilson interval
    excludes the global rate; everything else is "not_enough_data", no matter
    how lopsided the raw percentage looks (presentation-spec honesty contract).
    """
    low, high = wilson_interval(wins, n, z)
    if low > global_rate:
        return VERDICT_STRENGTH
    if high < global_rate:
        return VERDICT_WEAKNESS
    return VERDICT_NOT_ENOUGH_DATA
