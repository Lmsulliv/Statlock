"""Patch-notes-assisted era candidate detection (docs/presentation-spec.md).

The system proposes, you decide: we score Steam News posts and flag the
era-worthy ones as pending candidates; confirming/dismissing is a human
step in the era manager UI (Phase 5). Detection is idempotent — UNIQUE
(post_url) means re-scanning the feed never duplicates a candidate.

Calibration stance (docs/presentation-spec.md): "flag generously, dismiss
freely." A heuristic can count change lines but can't know a single urn-rework
line outweighs forty number tweaks, so we flag any patch with at least one
change line rather than gating on the title prefix. The title is kept only as a
floor for hero releases (≈0 change lines but era-worthy); you dismiss the false
positives with one click.
"""
import json
import logging
import re

from ingest.client import archive_response
from ingest.util import unix_to_iso, utcnow

log = logging.getLogger(__name__)

STEAM_NEWS_URL = (
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    "?appid=1422450&count=30&maxlength=0"
)
VALVE_FEED = "steam_community_announcements"
SCORE_THRESHOLD = 1  # flag generously; you dismiss freely (presentation-spec)

# A change line is "[p]- ..." in the BBCode-ish single-line contents.
_CHANGE_LINE_RE = re.compile(r"\[p\]\s*-\s")


def score_post(title: str, contents: str) -> tuple[int, float]:
    """Return (change_line_count, score). We flag any patch with at least one
    change line, so the title prefix is no longer a flagging requirement -- a
    small "Minor Update" (e.g. an urn rework) surfaces just like a big one.

    Hero-release posts carry ~0 change lines but reshape the meta, so posts that
    aren't routine "Minor Update"s get a floor that still clears the threshold.
    (Interim heuristic -- start loose, tighten once a month of candidates is
    seen.)"""
    change_lines = len(_CHANGE_LINE_RE.findall(contents or ""))
    minor = title.startswith("Minor Update")
    score = change_lines if minor else max(change_lines, SCORE_THRESHOLD)
    return change_lines, score


def detect_era_candidates(conn, client, *, now=utcnow) -> int:
    """Poll Steam News, flag era-worthy posts as pending candidates.
    Returns the number of new candidates inserted."""
    fetched_at = now().isoformat()
    status, _headers, body = client.get(STEAM_NEWS_URL)
    archive_response(conn, STEAM_NEWS_URL, status, body, fetched_at)
    if status != 200:
        log.warning("era detection: Steam News HTTP %s", status)
        return 0

    posts = json.loads(body).get("appnews", {}).get("newsitems", [])
    inserted = 0
    for post in posts:
        if post.get("feedname") != VALVE_FEED:
            continue  # only Valve's own Community Announcements
        change_lines, score = score_post(post.get("title", ""), post.get("contents", ""))
        if score < SCORE_THRESHOLD:
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO era_candidates"
            " (post_url, post_title, posted_at, change_lines, score, status)"
            " VALUES (?, ?, ?, ?, ?, 'pending')",
            (post.get("url"), post.get("title"), unix_to_iso(post["date"]), change_lines, score),
        )
        if cursor.rowcount:
            inserted += 1
            log.info("era candidate flagged: %r (score %.0f)", post.get("title"), score)
    conn.commit()
    return inserted
