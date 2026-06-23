"""Pure statistics functions: Wilson interval, Bayesian shrinkage, verdicts.

All statistics math for the app lives here (CLAUDE.md hard rule 1) and is
imported everywhere else. No database, no network, stdlib math only.
Formulas follow docs/data-model.md, "Statistics layer".
"""
import math
from collections.abc import Sequence
from statistics import fmean, stdev

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


# ── Continuous metrics ────────────────────────────────────────────────────────
# Win rate is a proportion (a 0/1 outcome), so Wilson + Beta shrinkage fit it.
# The other per-match metrics -- net worth, last hits, denies, player/obj damage,
# healing, the KDA components -- are *continuous*, and those binomial tools do
# not apply (their variance is fixed by the rate, which a mean does not have).
# A continuous metric instead gets a Student-t interval on its sample mean.

# Two-sided Student-t critical values, indexed by degrees of freedom (n - 1),
# for the two confidence bands the verdict uses: 95% (alpha = .05) and 80%
# (alpha = .20). Degrees of freedom = the number of values free to vary once the
# mean is pinned, i.e. n - 1. For df >= 31 we fall back to the normal z
# (Z_CLEAR / Z_LEAN): by df 30 the t critical value is within ~0.05 of the
# normal, the textbook "t -> z" crossover. Verify against any Student-t table.
_T_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
         8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
         14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
         20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
         26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}
_T_80 = {1: 3.078, 2: 1.886, 3: 1.638, 4: 1.533, 5: 1.476, 6: 1.440, 7: 1.415,
         8: 1.397, 9: 1.383, 10: 1.372, 11: 1.363, 12: 1.356, 13: 1.350,
         14: 1.345, 15: 1.341, 16: 1.337, 17: 1.333, 18: 1.330, 19: 1.328,
         20: 1.325, 21: 1.323, 22: 1.321, 23: 1.319, 24: 1.318, 25: 1.316,
         26: 1.315, 27: 1.314, 28: 1.313, 29: 1.311, 30: 1.310}


def _t_critical(df: int, confidence: float) -> float:
    """Two-sided Student-t critical value: table for df 1-30, z for df >= 31."""
    if confidence == 0.95:
        return _T_95.get(df, Z_CLEAR)
    if confidence == 0.80:
        return _T_80.get(df, Z_LEAN)
    raise ValueError("confidence must be 0.95 or 0.80")


def mean_interval(
    values: Sequence[float], confidence: float = 0.95
) -> tuple[float, float, float]:
    """Sample mean of a continuous metric with a t-based confidence interval.

    Returns (mean, low, high), where (low, high) = mean +/- t(n-1, conf) * s /
    sqrt(n) and s is the sample standard deviation (n - 1 denominator).

    Method and assumptions: a Student-t interval on the sample mean. A
    *t-interval* is the small-sample analogue of mean +/- z * standard-error;
    the wider t multiplier accounts for estimating the spread from the data
    itself. It assumes the sampling distribution of the MEAN is approximately
    normal (the Central Limit Theorem makes this hold for the per-match metrics
    -- net worth, damage, healing, ... -- at the sample sizes these screens use)
    and that the metric has finite variance. It is deterministic and fast, which
    is why it is preferred here over a bootstrap CI. It is NOT for win rate: a
    0/1 outcome is binomial, so use wilson_interval for that.

    Edge cases mirror wilson_interval's "no information -> widest interval":
    with n <= 1 the spread of the mean is unknown, so the interval is all of
    (-inf, +inf). An empty sample has no mean and raises ValueError. Only the
    two tabulated confidence levels (0.95, 0.80) are supported.
    """
    n = len(values)
    if n == 0:
        raise ValueError("mean of an empty sample is undefined")
    mean = fmean(values)
    if n == 1:
        return (mean, float("-inf"), float("inf"))
    halfwidth = _t_critical(n - 1, confidence) * stdev(values) / math.sqrt(n)
    return (mean, mean - halfwidth, mean + halfwidth)


def mean_verdict(values: Sequence[float], baseline_mean: float) -> str:
    """Confidence-aware call for a continuous-metric mean against a baseline mean.

    The direct mirror of verdict() for proportions, reusing the same five tiers
    and the same VERDICT_FLOOR philosophy so thin samples never earn a verdict:
    - Below VERDICT_FLOOR values -> always not_enough_data.
    - "clear" when the 95% t-interval of the personal mean excludes the baseline.
    - "leaning" when only the looser 80% t-interval excludes it.
    - otherwise not_enough_data.

    "strength" means the personal mean sits *above* the baseline and "weakness"
    *below* -- a value-neutral direction. Whether higher is good (net worth) or
    bad (deaths) is the caller's call, not this layer's.

    No shrinkage term, unlike the proportion verdict. A principled normal-normal
    pull toward the baseline would need an arbitrary per-metric prior-variance
    knob (complexity without clear benefit), and the simple pseudo-observation
    pull that would mirror shrunk_rate, (n*mean + k*baseline) / (n + k), is a
    convex combination of the mean and the baseline -- so it always lands on the
    sample mean's side of the baseline. The "shrinkage agrees on direction"
    guard would therefore be vacuous and change no verdict. Shrinkage stays with
    win rate; see docs/data-model.md.
    """
    if len(values) < VERDICT_FLOOR:
        return VERDICT_NOT_ENOUGH_DATA

    _, low, high = mean_interval(values, 0.95)
    if low > baseline_mean:
        return VERDICT_CLEAR_STRENGTH
    if high < baseline_mean:
        return VERDICT_CLEAR_WEAKNESS

    _, lean_low, lean_high = mean_interval(values, 0.80)
    if lean_low > baseline_mean:
        return VERDICT_LEANING_STRENGTH
    if lean_high < baseline_mean:
        return VERDICT_LEANING_WEAKNESS
    return VERDICT_NOT_ENOUGH_DATA
