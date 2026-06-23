"""Laning-phase domain helpers: where "end of laning" is, and how to read a
player's early-game snapshot out of the per-player `stats[]` time series.

Lives in stats/ so the lane-end definition is single-sourced (CLAUDE.md hard
rule 1 keeps the math here; this is the one constant everything else imports) and
so ingest can reuse it without importing api. Pure functions only -- no database,
no network. See docs/api-findings.md, "Per-player `stats[]` time series".
"""
from collections.abc import Sequence

# The lane-end mark, in seconds. ~10 minutes is the standard laning-phase length.
# The series samples at 180/360/540/720/900/1200/... (api-findings), so there is
# no snapshot exactly at 600 s; laning_mark() snaps to the latest snapshot at or
# before this, i.e. 540 s in a normal match. One named constant, so the report,
# the docs, and any future tuning all move together.
LANE_END_S = 600


def laning_mark(stats: Sequence[dict] | None) -> dict | None:
    """The player's stats snapshot that represents the end of laning, or None.

    Given one player's `stats[]` series (each entry a dict with `time_stamp_s`),
    return the latest snapshot whose `time_stamp_s <= LANE_END_S`. That is the
    "net worth at ~the 10-minute mark" the laning report compares: at a fixed
    time, every player's cumulative net worth / last hits / denies are directly
    comparable without per-minute normalization.

    Returns None when there is nothing honest to report -- an empty or missing
    series, or a match so short it never reached the lane-end mark (only snapshots
    after it, or none at all). A None mark means "we don't know," never a
    fabricated zero, so it can't poison a baseline average (the same rule
    finals_from_stats follows for damage/healing).

    Snapshots with a missing/None `time_stamp_s` are ignored rather than trusted.
    """
    if not stats:
        return None
    eligible = [s for s in stats
                if s.get("time_stamp_s") is not None and s["time_stamp_s"] <= LANE_END_S]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s["time_stamp_s"])
