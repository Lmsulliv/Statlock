"""Tests: continuous-metric statistics (t-interval on a mean, mean verdict).

The binomial twin of these lives in test_stats.py. These are pure-function
tests: no database, no network, no fixtures. Hypothesis property tests follow
the same replay conventions documented at the top of test_stats.py.
"""
import math
from statistics import fmean, stdev

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from stats import (
    VERDICT_CLEAR_STRENGTH,
    VERDICT_CLEAR_WEAKNESS,
    VERDICT_FLOOR,
    VERDICT_LEANING_STRENGTH,
    VERDICT_LEANING_WEAKNESS,
    VERDICT_NOT_ENOUGH_DATA,
    Z_CLEAR,
    mean_interval,
    mean_verdict,
)


# ── Shared strategies ─────────────────────────────────────────────────────────

# Bounded, finite floats: big enough to exercise real spreads, small enough that
# squaring them for the variance can never overflow.
metric_value = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)


@st.composite
def metric_samples(draw, min_size=1, max_size=20):
    """A non-empty sample of a continuous metric."""
    return draw(st.lists(metric_value, min_size=min_size, max_size=max_size))


# A baseline mean to compare a sample against; same float range as the values.
any_baseline = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)


# ── mean_interval: known value ───────────────────────────────────────────────

def test_mean_interval_known_value():
    # Textbook sample [2,4,4,4,5,5,7,9]: mean 5, sample stdev sqrt(32/7) ≈ 2.138
    # (n-1 denominator), n 8, t(df=7, 95%) = 2.365.
    # halfwidth = 2.365 * 2.138090 / sqrt(8) ≈ 1.787772.
    mean, low, high = mean_interval([2, 4, 4, 4, 5, 5, 7, 9])
    assert mean == pytest.approx(5.0)
    assert low == pytest.approx(3.212228, abs=1e-3)
    assert high == pytest.approx(6.787772, abs=1e-3)


def test_mean_interval_is_symmetric_about_the_mean():
    mean, low, high = mean_interval([10, 12, 14, 16, 18])
    assert (low + high) / 2 == pytest.approx(mean)


# ── mean_interval: edge cases ────────────────────────────────────────────────

def test_mean_interval_empty_sample_raises():
    with pytest.raises(ValueError):
        mean_interval([])


def test_mean_interval_single_value_is_total_ignorance():
    # One game: the mean is known but its spread is not, so the interval is the
    # whole real line — the continuous mirror of wilson(0, 0) -> (0, 1).
    mean, low, high = mean_interval([42.0])
    assert mean == 42.0
    assert low == float("-inf")
    assert high == float("inf")


def test_mean_interval_zero_variance_collapses_to_a_point():
    mean, low, high = mean_interval([7.0, 7.0, 7.0, 7.0, 7.0])
    assert (mean, low, high) == (7.0, 7.0, 7.0)


def test_mean_interval_rejects_unsupported_confidence():
    with pytest.raises(ValueError):
        mean_interval([1.0, 2.0, 3.0], confidence=0.99)


def test_mean_interval_80_band_is_narrower_than_95():
    _, low95, high95 = mean_interval([1, 2, 3, 4, 5, 6], confidence=0.95)
    _, low80, high80 = mean_interval([1, 2, 3, 4, 5, 6], confidence=0.80)
    assert (high80 - low80) < (high95 - low95)


# ── mean_interval: property tests ────────────────────────────────────────────

@given(metric_samples(min_size=2))
def test_property_interval_contains_the_mean(values):
    """The sample mean always lies inside its own confidence interval."""
    mean, low, high = mean_interval(values)
    assert low <= mean <= high


@given(metric_samples(min_size=2), st.floats(min_value=1.5, max_value=10))
def test_property_interval_widens_with_variance(values, scale):
    """Scaling every deviation from the mean (more spread, same n) widens it."""
    assume(stdev(values) > 1e-3)
    m = fmean(values)
    spread = [m + scale * (v - m) for v in values]
    _, low, high = mean_interval(values)
    _, wide_low, wide_high = mean_interval(spread)
    assert (wide_high - wide_low) > (high - low)


@given(metric_samples(min_size=2), st.integers(min_value=2, max_value=5))
def test_property_interval_narrows_with_more_data(values, copies):
    """Tiling the sample (≈same spread, more data) strictly narrows the interval."""
    assume(stdev(values) > 1e-3)
    _, low, high = mean_interval(values)
    _, more_low, more_high = mean_interval(values * copies)
    assert (more_high - more_low) < (high - low)


@given(metric_samples(min_size=2))
def test_property_t_interval_never_narrower_than_normal(values):
    """t(df) ≥ z for every df, so the t-interval is at least as wide as the
    naive normal-z interval built from the same mean, stdev, and n."""
    _, low, high = mean_interval(values, 0.95)
    n = len(values)
    z_halfwidth = Z_CLEAR * stdev(values) / math.sqrt(n)
    assert (high - low) / 2 >= z_halfwidth - 1e-9


# ── mean_verdict (5-tier, confidence-aware) ──────────────────────────────────

# A tight cluster around 10: mean 10, sample stdev ≈ 0.577, n 7. Its bands are
#   95%: 10 ± 0.534 -> (9.466, 10.534)
#   80%: 10 ± 0.314 -> (9.686, 10.314)
# so a baseline's position relative to those edges pins each verdict tier.
_CLUSTER = [10, 10, 10, 11, 9, 10, 10]


def test_verdict_below_floor_never_earns_a_verdict():
    # Four games perfectly above the baseline still can't earn a verdict.
    assert mean_verdict([100, 101, 99, 100], baseline_mean=0) == VERDICT_NOT_ENOUGH_DATA


def test_verdict_clear_strength_when_95_excludes_below():
    assert mean_verdict(_CLUSTER, baseline_mean=0) == VERDICT_CLEAR_STRENGTH


def test_verdict_clear_weakness_when_95_excludes_above():
    assert mean_verdict(_CLUSTER, baseline_mean=100) == VERDICT_CLEAR_WEAKNESS


def test_verdict_leaning_strength_when_only_80_excludes_below():
    # 9.6 sits inside the 95% band but below the 80% lower edge (9.686).
    assert mean_verdict(_CLUSTER, baseline_mean=9.6) == VERDICT_LEANING_STRENGTH


def test_verdict_leaning_weakness_when_only_80_excludes_above():
    # 10.4 sits inside the 95% band but above the 80% upper edge (10.314).
    assert mean_verdict(_CLUSTER, baseline_mean=10.4) == VERDICT_LEANING_WEAKNESS


def test_verdict_baseline_at_the_mean_is_not_enough_data():
    assert mean_verdict(_CLUSTER, baseline_mean=10.0) == VERDICT_NOT_ENOUGH_DATA


# ── mean_verdict: property tests ─────────────────────────────────────────────

@given(metric_samples(min_size=1), any_baseline)
def test_property_clear_verdict_matches_95_exclusion(values, baseline):
    """A "clear" verdict is exactly the 95% t-exclusion rule; any other verdict
    above the floor means the 95% band contains the baseline."""
    v = mean_verdict(values, baseline)
    n = len(values)
    if v == VERDICT_CLEAR_STRENGTH:
        _, low, _ = mean_interval(values, 0.95)
        assert low > baseline
    elif v == VERDICT_CLEAR_WEAKNESS:
        _, _, high = mean_interval(values, 0.95)
        assert high < baseline
    elif n >= VERDICT_FLOOR:
        _, low, high = mean_interval(values, 0.95)
        assert low <= baseline <= high


@given(metric_samples(min_size=5), any_baseline)
def test_property_verdict_direction_agrees_with_the_mean(values, baseline):
    """Any strength tier means the personal mean is above baseline; weakness below."""
    v = mean_verdict(values, baseline)
    mean = fmean(values)
    if v in (VERDICT_CLEAR_STRENGTH, VERDICT_LEANING_STRENGTH):
        assert mean > baseline
    elif v in (VERDICT_CLEAR_WEAKNESS, VERDICT_LEANING_WEAKNESS):
        assert mean < baseline
