"""Pure recurring-player helper: from one DB pass of co-occurrence counts, keep
the players you've shared enough matches with and split them into teammates and
opponents.

Like stats/__init__.py and stats/sessions.py this is pure (CLAUDE.md hard rule 1):
no database, no network. The service layer feeds in the plain count rows the
self-join returns and applies the Wilson/verdict machinery to the survivors.
"""

# A player must share at least this many of your matches to count as "recurring"
# and appear at all. Deliberately below the verdict floor (stats.VERDICT_FLOOR is
# 5): a 3-4 game co-player IS listed but still reads not_enough_data, so the gate
# (who is recurring) and the honesty floor (who earns a verdict) stay separate.
# Single source of truth; mirrored in docs/data-model.md.
MIN_CO_OCCURRENCE = 3


def split_recurring(rows: list[dict], min_co_occurrence: int = MIN_CO_OCCURRENCE) -> dict:
    """Split co-occurrence counts into teammates and opponents.

    `rows` are the count rows from one DB pass, each a dict with "account_id",
    "same_team" (1 you played WITH them, 0 you played AGAINST them), "games", and
    "wins" (your wins in those shared games). Only players at or above
    `min_co_occurrence` shared games are kept; each side is sorted most-shared
    first (ties broken by account_id) so the "top recurring" players lead. The
    same account can land on both sides -- a teammate in some games and an
    opponent in others -- and is counted and judged separately on each.
    """
    teammates, opponents = [], []
    for r in rows:
        if r["games"] < min_co_occurrence:
            continue
        entry = {"account_id": r["account_id"], "games": r["games"], "wins": r["wins"]}
        (teammates if r["same_team"] else opponents).append(entry)

    key = lambda e: (-e["games"], e["account_id"])
    teammates.sort(key=key)
    opponents.sort(key=key)
    return {"teammates": teammates, "opponents": opponents}
