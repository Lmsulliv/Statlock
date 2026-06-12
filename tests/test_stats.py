"""Tests: pure statistics functions (Wilson interval, shrinkage, verdict).

These are pure-function tests: no database, no network, no fixtures.

--- Hypothesis: reproducing a failure ---
When a property test fails Hypothesis prints the minimal counterexample, e.g.:

    Falsifying example: test_property_wilson_in_unit_interval(wins_n=(0, 1))

and the decorator needed to replay it exactly:

    @reproduce_failure('6.x.y', b'AXicY2BgYGQAAkAGAA==')
    def test_property_wilson_in_unit_interval(wins_n): ...

Paste that decorator directly above the failing test and re-run to get the
same failure every time. Failures are also written to .hypothesis/ and
replayed automatically on the next run so they don't silently disappear
between CI invocations.
"""
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from stats import shrunk_rate, verdict, wilson_interval


# ── Shared strategies ─────────────────────────────────────────────────────────

@st.composite
def wins_and_n(draw, min_n=1, max_n=10_000):
    """Generates (wins, n) with 0 ≤ wins ≤ n."""
    n = draw(st.integers(min_value=min_n, max_value=max_n))
    wins = draw(st.integers(min_value=0, max_value=n))
    return wins, n


# Valid probability including 0.0 and 1.0 extremes; NaN/inf excluded.
any_rate = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


# ── wilson_interval: known values ────────────────────────────────────────────

def test_wilson_known_value_wallis_2013():
    # Published worked example (Wallis 2013, "Binomial confidence intervals
    # and contingency tests"): w=1, n=10, z=1.96 gives roughly (0.0179, 0.4042).
    # Hand-computed from the spec formula: center=0.211016, halfwidth=0.193139.
    low, high = wilson_interval(1, 10)
    assert low == pytest.approx(0.0179, abs=1e-3)
    assert high == pytest.approx(0.4042, abs=1e-3)


def test_wilson_symmetric_at_half():
    # p̂ = 0.5 makes the center land exactly on 0.5 and the interval symmetric.
    low, high = wilson_interval(5, 10)
    assert (low + high) / 2 == pytest.approx(0.5)
    assert low == pytest.approx(1 - high)


# ── wilson_interval: edge cases ──────────────────────────────────────────────

def test_wilson_n_zero_is_total_ignorance():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_one_game_one_win():
    # At p̂ = 1 the Wilson upper bound is exactly 1 (the formula collapses:
    # high = (1 + z²/n)/(1 + z²/n)); only the lower bound pulls inward.
    low, high = wilson_interval(1, 1)
    assert high == 1.0
    assert 0.0 < low < 1.0


def test_wilson_one_game_one_loss_mirrors_one_win():
    loss_low, loss_high = wilson_interval(0, 1)
    win_low, win_high = wilson_interval(1, 1)
    assert loss_low == 0.0  # mirror of high == 1.0 at p̂ = 1
    assert loss_high == pytest.approx(1 - win_low)


def test_wilson_all_wins_lower_bound_pulls_inward():
    # 20/20: upper bound stays pinned at 1, lower bound is well inside.
    low, high = wilson_interval(20, 20)
    assert high == 1.0
    assert 0.0 < low < 1.0
    # n/(n + z²) closed form for the w = n lower bound
    assert low == pytest.approx(20 / (20 + 1.96**2))


def test_wilson_all_losses_upper_bound_pulls_inward():
    low, high = wilson_interval(0, 20)
    assert low == 0.0
    assert 0.0 < high < 1.0


# ── wilson_interval: property tests ──────────────────────────────────────────

@given(wins_and_n(min_n=0))
def test_property_wilson_in_unit_interval(wins_n):
    """Interval is always a valid sub-range of [0, 1], including at n=0."""
    wins, n = wins_n
    low, high = wilson_interval(wins, n)
    assert 0.0 <= low <= high <= 1.0


@given(wins_and_n())
def test_property_wilson_contains_point_estimate(wins_n):
    """Observed win rate must lie inside its own confidence interval."""
    wins, n = wins_n
    low, high = wilson_interval(wins, n)
    assert low <= wins / n <= high


@given(wins_and_n(min_n=1, max_n=5_000))
def test_property_wilson_narrows_with_more_data(wins_n):
    """Doubling sample size at the same exact win rate strictly narrows the interval."""
    wins, n = wins_n
    # Skip p̂ = 0 and p̂ = 1: the pinned boundary means the interval effectively
    # only has one moving edge, and the width comparison still holds but the
    # arithmetic with the clamped endpoints obscures the invariant.
    assume(0 < wins < n)
    low1, high1 = wilson_interval(wins, n)
    low2, high2 = wilson_interval(wins * 2, n * 2)
    assert (high1 - low1) > (high2 - low2)


@given(wins_and_n())
def test_property_wilson_reflection_symmetry(wins_n):
    """Swapping wins ↔ losses mirrors the interval around 0.5."""
    wins, n = wins_n
    low, high = wilson_interval(wins, n)
    mlow, mhigh = wilson_interval(n - wins, n)
    assert low == pytest.approx(1 - mhigh)
    assert high == pytest.approx(1 - mlow)


# ── shrunk_rate ──────────────────────────────────────────────────────────────

def test_shrunk_rate_few_games_barely_moves_off_global():
    # Spec narrative: with 3 games the adjusted rate barely moves.
    # k=10, g=0.5, w=3, n=3: (3 + 10*0.5) / (3 + 10) = 8/13.
    assert shrunk_rate(3, 3, 0.5) == pytest.approx(8 / 13)


def test_shrunk_rate_many_games_dominated_by_own_data():
    # k=10, g=0.5, w=30, n=40: (30 + 5) / (40 + 10) = 0.7.
    assert shrunk_rate(30, 40, 0.5) == pytest.approx(0.7)


def test_shrunk_rate_n_zero_returns_global_rate():
    assert shrunk_rate(0, 0, 0.43) == pytest.approx(0.43)


def test_shrunk_rate_converges_to_personal_rate_as_n_grows():
    # 60% personal rate vs 50% global: gap to 0.6 shrinks as n grows.
    g = 0.5
    gaps = [abs(shrunk_rate(int(n * 0.6), n, g) - 0.6) for n in (10, 100, 1000)]
    assert all(a > b for a, b in zip(gaps, gaps[1:]))


# ── shrunk_rate: property tests ───────────────────────────────────────────────

@given(wins_and_n(), any_rate)
def test_property_shrunk_rate_between_raw_and_global(wins_n, g):
    """Adjusted rate always lies strictly between the raw rate and the global rate.

    When they're very close (< 1e-9 apart), float arithmetic can't reliably
    place adjusted strictly between them, so we skip those cases.
    """
    wins, n = wins_n
    personal = wins / n
    assume(abs(personal - g) > 1e-9)
    adjusted = shrunk_rate(wins, n, g)
    assert min(personal, g) < adjusted < max(personal, g)


# ── verdict ──────────────────────────────────────────────────────────────────

def test_verdict_two_games_never_earns_a_verdict():
    # Presentation-spec acceptance scenario 1: a matchup with 2 games never
    # displays a verdict, regardless of record.
    assert verdict(2, 2, 0.5) == "not_enough_data"
    assert verdict(0, 2, 0.5) == "not_enough_data"


def test_verdict_n_zero_is_not_enough_data():
    assert verdict(0, 0, 0.5) == "not_enough_data"


def test_verdict_strength_when_interval_excludes_global_below():
    assert verdict(90, 100, 0.5) == "strength"


def test_verdict_weakness_when_interval_excludes_global_above():
    assert verdict(10, 100, 0.5) == "weakness"


def test_verdict_lopsided_but_tiny_sample_is_not_enough_data():
    assert verdict(1, 1, 0.5) == "not_enough_data"


# ── verdict: property tests ───────────────────────────────────────────────────

@given(wins_and_n(min_n=0), any_rate)
def test_property_verdict_consistent_with_wilson_exclusion(wins_n, g):
    """Verdict is exactly the Wilson-exclusion rule — no looser, no stricter."""
    wins, n = wins_n
    low, high = wilson_interval(wins, n)
    v = verdict(wins, n, g)
    if v == "strength":
        assert low > g
    elif v == "weakness":
        assert high < g
    else:
        assert v == "not_enough_data"
        assert low <= g <= high


@given(wins_and_n(), any_rate)
def test_property_verdict_direction_agrees_with_raw_rate(wins_n, g):
    """Strength always means personal win rate is above global; weakness below."""
    wins, n = wins_n
    v = verdict(wins, n, g)
    if v == "strength":
        assert wins / n > g
    elif v == "weakness":
        assert wins / n < g
