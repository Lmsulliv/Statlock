"""Pure death-pattern shaping (stats/deaths.py): binning, labels, and the
zero-filled per-game samples build_timeline hands the service. No DB, no network.
"""
from stats.deaths import (
    DEATH_TIMELINE_CAP_MIN,
    bin_label,
    build_timeline,
    minute_bin,
)


# ── minute_bin ───────────────────────────────────────────────────────────────

def test_minute_bin_floors_seconds_into_minutes():
    assert minute_bin(0) == 0
    assert minute_bin(59) == 0
    assert minute_bin(60) == 1
    assert minute_bin(125) == 2


def test_minute_bin_clamps_long_games_into_the_trailing_bin():
    cap = DEATH_TIMELINE_CAP_MIN
    assert minute_bin(cap * 60) == cap
    assert minute_bin(cap * 60 + 5000) == cap     # very late deaths fold into the cap


# ── bin_label ────────────────────────────────────────────────────────────────

def test_bin_label_reads_as_a_minute_range_and_an_open_tail():
    assert bin_label(0) == "0-1m"
    assert bin_label(12) == "12-13m"
    assert bin_label(DEATH_TIMELINE_CAP_MIN) == f"{DEATH_TIMELINE_CAP_MIN}m+"


# ── build_timeline ───────────────────────────────────────────────────────────

def test_per_game_counts_are_zero_filled_to_the_game_count():
    # 4 scoped games; the player died twice in minute 2, both in the SAME game.
    bins = build_timeline(
        game_count=4,
        death_times=[(10, 130), (10, 140)],   # match 10, two deaths in minute 2
        baseline_deaths_by_minute={},
        baseline_games=0,
    )
    minute2 = next(b for b in bins if b["minute"] == 2)
    assert minute2["deaths"] == 2
    # One game had 2 deaths in the bin; the other three contribute a real 0.
    assert sorted(minute2["per_game_counts"]) == [0, 0, 0, 2]
    assert len(minute2["per_game_counts"]) == 4


def test_deaths_in_different_games_stay_separate_samples():
    bins = build_timeline(
        game_count=3,
        death_times=[(1, 130), (2, 130)],     # one death each in two different games
        baseline_deaths_by_minute={},
        baseline_games=0,
    )
    minute2 = next(b for b in bins if b["minute"] == 2)
    assert sorted(minute2["per_game_counts"]) == [0, 1, 1]


def test_axis_runs_from_zero_to_the_last_observed_bin_either_side():
    # Personal tops out at minute 2; population has a death at minute 5.
    bins = build_timeline(
        game_count=2,
        death_times=[(1, 130)],
        baseline_deaths_by_minute={5: 4},
        baseline_games=8,
    )
    assert [b["minute"] for b in bins] == [0, 1, 2, 3, 4, 5]


def test_baseline_mean_is_population_deaths_over_games_or_none():
    bins = build_timeline(
        game_count=2,
        death_times=[(1, 130)],
        baseline_deaths_by_minute={2: 6},
        baseline_games=4,
    )
    minute2 = next(b for b in bins if b["minute"] == 2)
    assert minute2["baseline_mean"] == 6 / 4
    assert minute2["baseline_games"] == 4
    # A bin with no population deaths is still 0.0 (the population just never died
    # there), not None -- None is reserved for "no population at all".
    minute0 = next(b for b in bins if b["minute"] == 0)
    assert minute0["baseline_mean"] == 0.0


def test_no_population_means_no_baseline():
    bins = build_timeline(
        game_count=2,
        death_times=[(1, 130)],
        baseline_deaths_by_minute={},
        baseline_games=0,
    )
    assert all(b["baseline_mean"] is None for b in bins)


def test_population_tail_folds_into_the_capped_bin():
    cap = DEATH_TIMELINE_CAP_MIN
    bins = build_timeline(
        game_count=1,
        death_times=[],
        baseline_deaths_by_minute={cap: 2, cap + 4: 3, cap + 50: 5},
        baseline_games=10,
    )
    capped = next(b for b in bins if b["minute"] == cap)
    assert capped["baseline_mean"] == (2 + 3 + 5) / 10
    assert bins[-1]["minute"] == cap          # nothing past the cap
