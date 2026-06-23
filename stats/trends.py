"""Pure time-bucketing helpers for the Trends screen: split a time-ordered match
stream into rolling windows (a moving average) or calendar buckets (per week /
per month).

Like stats/__init__.py and stats/sessions.py this is pure grouping (CLAUDE.md
hard rule 1): no database, no network. It only slices and groups the plain,
time-ordered dicts the service feeds in; the service then applies the
Wilson/t-interval/VERDICT_FLOOR machinery to each bucket's members. Keeping the
math out of here means one tested honesty floor serves win rate and every
continuous metric, and the grouping can be unit-tested without a DB.

Each input item is a dict with at least "start_time" (ISO-8601). Helpers return
buckets shaped {"key", "label", "items"} -- "key" sorts chronologically, "label"
is for display, and "items" is the member slice the service aggregates. Labels
are ASCII (plain '-' ranges) so the CLI never trips a cp1252 console.
"""
from datetime import date, datetime, timedelta

# Default moving-average width: the last N games. Exposed so the API/CLI default
# and this module never drift. User-adjustable per request (window_games).
TRENDS_WINDOW_DEFAULT = 20

_MONTH = "%b"   # abbreviated month name, e.g. "Jun"


def _week_label(monday: date, sunday: date) -> str:
    """A readable Mon-Sun range, e.g. "Jun 15-21" or "Jun 29-Jul 5" across a
    month boundary. ASCII '-' (not an en-dash) keeps the CLI console-safe."""
    if monday.month == sunday.month:
        return f"{monday:{_MONTH}} {monday.day}-{sunday.day}"
    return f"{monday:{_MONTH}} {monday.day}-{sunday:{_MONTH}} {sunday.day}"


def calendar_key(start_time_iso: str, granularity: str) -> tuple[str, str]:
    """Map a match's ISO-8601 start time to its (sort_key, label) for one
    calendar bucket.

    week  -> ("2026-W25", "Jun 15-21")  using the ISO week (Mon-Sun); the ISO
             year can differ from the calendar year at the turn of January, so
             the key carries the ISO year to keep late-December weeks ordered.
    month -> ("2026-06", "Jun 2026").

    Keys are zero-padded so plain string sort is chronological.
    """
    dt = datetime.fromisoformat(start_time_iso)
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        sunday = monday + timedelta(days=6)
        return f"{iso_year}-W{iso_week:02d}", _week_label(monday, sunday)
    if granularity == "month":
        return f"{dt.year}-{dt.month:02d}", dt.strftime(f"{_MONTH} %Y")
    raise ValueError(f"unknown granularity {granularity!r} (expected week or month)")


def bucket_by_calendar(items: list[dict], granularity: str) -> list[dict]:
    """Group time-ordered items into calendar buckets, oldest bucket first.

    Items need not arrive sorted: buckets are returned in key order, which is
    chronological. Empty buckets never appear (a week/month with no games is
    simply absent), mirroring stats.sessions._bucketed.
    """
    buckets: dict[str, dict] = {}
    for item in items:
        key, label = calendar_key(item["start_time"], granularity)
        bucket = buckets.setdefault(key, {"key": key, "label": label, "items": []})
        bucket["items"].append(item)
    return [buckets[k] for k in sorted(buckets)]


def _day_label(start_time_iso: str) -> str:
    dt = datetime.fromisoformat(start_time_iso)
    return f"{dt:{_MONTH}} {dt.day}"


def rolling_windows(items: list[dict], window: int) -> list[dict]:
    """A trailing moving average: one bucket per match, each holding the last
    `window` matches up to and including it (fewer at the very start).

    `items` must be time-ordered, oldest first. Early buckets are deliberately
    partial -- the service flags those below VERDICT_FLOOR as not-enough-data, so
    the warm-up of the average reads honestly rather than being hidden. Each
    bucket's label is the date of its latest match (its x position in time).
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    out: list[dict] = []
    for i, item in enumerate(items):
        members = items[max(0, i - window + 1): i + 1]
        out.append({"key": str(i), "label": _day_label(item["start_time"]),
                    "items": members})
    return out
