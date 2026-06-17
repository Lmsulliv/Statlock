"""Pure tilt-analysis helpers: group matches into play sessions and bucket them
by position-within-session and by preceding-loss-streak.

Like stats/__init__.py this is pure math/grouping (CLAUDE.md hard rule 1): no
database, no network. The service layer feeds in plain, time-ordered dicts and
applies the Wilson/verdict machinery to the counts these return.

A "session" is a single sitting. The API exposes no session id, so we infer one
from the time gaps between consecutive matches: a gap of SESSION_GAP_S or more
starts a new session. See docs/data-model.md, "Session / tilt analysis".
"""
from datetime import datetime

# A break of three hours or more ends a sitting. The one knob of the tilt model;
# documented here and in docs/data-model.md so there's a single source of truth.
SESSION_GAP_S = 3 * 60 * 60


def group_sessions(matches: list[dict], gap_s: int = SESSION_GAP_S) -> list[list[dict]]:
    """Split time-ordered matches into sessions.

    `matches` must be ordered ascending by start_time; each is a dict with at
    least "start_time" (ISO-8601) and "won". A new session begins whenever the
    gap to the previous match is `gap_s` seconds or more (boundary inclusive).
    """
    sessions: list[list[dict]] = []
    prev_time: datetime | None = None
    for m in matches:
        t = datetime.fromisoformat(m["start_time"])
        if prev_time is None or (t - prev_time).total_seconds() >= gap_s:
            sessions.append([])
        sessions[-1].append(m)
        prev_time = t
    return sessions


def _bucketed(pairs: list[tuple[int, dict]], cap: int, key: str) -> list[dict]:
    """Aggregate (bucket_key, match) pairs into ordered count rows.

    Keys at or above `cap` collapse into the single `cap` bucket (the "cap+"
    tail, flagged capped=True). Empty buckets are omitted so callers never
    render a row that no game falls into.
    """
    agg: dict[int, dict] = {}
    for raw_key, match in pairs:
        k = min(raw_key, cap)
        bucket = agg.setdefault(k, {key: k, "capped": k == cap, "games": 0, "wins": 0})
        bucket["games"] += 1
        bucket["wins"] += match["won"]
    return [agg[k] for k in sorted(agg)]


def by_session_index(sessions: list[list[dict]], cap: int = 6) -> list[dict]:
    """Win/game counts by 1-based position within a session.

    Position 1 is the first game of a sitting, 2 the second, and so on; positions
    at or past `cap` fold into a single "cap+" bucket. Returns ordered rows of
    {"index", "capped", "games", "wins"}.
    """
    pairs = [(i, m) for s in sessions for i, m in enumerate(s, start=1)]
    return _bucketed(pairs, cap, key="index")


def by_loss_streak(sessions: list[list[dict]], cap: int = 3) -> list[dict]:
    """Win/game counts by the run of consecutive losses immediately preceding a
    game, counted within its session.

    Streak 0 means a game played fresh or right after a win; the counter climbs
    with each loss and resets on any win (and at every session boundary, since
    each session is processed independently). Streaks at or past `cap` fold into
    a single "cap+" bucket. Returns ordered rows of
    {"streak", "capped", "games", "wins"}.
    """
    pairs: list[tuple[int, dict]] = []
    for session in sessions:
        streak = 0
        for m in session:
            pairs.append((streak, m))          # streak BEFORE this game's result
            streak = 0 if m["won"] else streak + 1
    return _bucketed(pairs, cap, key="streak")
