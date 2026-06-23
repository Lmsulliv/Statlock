"""Pure laning_mark selection: which stats[] snapshot represents end of laning.

stats.laning is the single source of the lane-end definition (LANE_END_S) and the
pure snapshot picker both ingest and the read layer rely on. These tests pin the
"snap to the latest snapshot <= 600 s" rule and the honest-None edge cases, with
no DB and no network (hard rule 3 holds trivially -- every call here is pure).
"""
from stats.laning import LANE_END_S, laning_mark

# The real cadence from api-findings: 180/360/540/720/900, then every 300 s.
CADENCE = [180, 360, 540, 720, 900, 1200]


def _series(times):
    """A minimal stats[] series: one snapshot per timestamp, net_worth = t so the
    chosen snapshot is identifiable by value."""
    return [{"time_stamp_s": t, "net_worth": t, "creep_kills": t // 10, "denies": 1}
            for t in times]


def test_picks_latest_snapshot_at_or_before_lane_end():
    # 540 is the last snapshot <= 600 on the real cadence (720 is past it).
    mark = laning_mark(_series(CADENCE))
    assert mark is not None
    assert mark["time_stamp_s"] == 540


def test_lane_end_is_ten_minutes():
    assert LANE_END_S == 600


def test_exact_lane_end_sample_is_eligible():
    # A hypothetical exact-600 snapshot is included (<= is inclusive) and wins
    # over the earlier 540.
    mark = laning_mark(_series([180, 360, 540, 600, 720]))
    assert mark["time_stamp_s"] == 600


def test_short_match_still_picks_last_snapshot_before_lane_end():
    # A game that ended in laning: only early snapshots exist, so the last one
    # (360) is the lane-end stand-in rather than None.
    mark = laning_mark(_series([180, 360]))
    assert mark["time_stamp_s"] == 360


def test_no_snapshot_before_lane_end_is_none():
    # A series whose first snapshot is already past the mark -> no laning sample.
    assert laning_mark(_series([720, 900])) is None


def test_empty_or_missing_series_is_none():
    assert laning_mark([]) is None
    assert laning_mark(None) is None


def test_snapshot_without_timestamp_is_ignored():
    # A malformed snapshot lacking time_stamp_s must not be trusted or crash; the
    # valid 360 snapshot is still chosen.
    series = [{"net_worth": 9}, {"time_stamp_s": 360, "net_worth": 360}]
    mark = laning_mark(series)
    assert mark["time_stamp_s"] == 360
