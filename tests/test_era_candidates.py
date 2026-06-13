"""Unit tests for ingest.eras: patch-notes-assisted era candidate detection.

Calibration values come from the real recorded Steam News posts
(docs/api-findings.md): the 05-22 Gameplay Update has 307 change lines,
the 06-04 Minor Update has 14, and the Apollo hero release has 0.
"""
from ingest.eras import detect_era_candidates, score_post

from tests.fakes import FakeClient, ManualNow, fixture_text, load_fixture


def post_by_prefix(prefix: str) -> dict:
    posts = load_fixture("steam_news.json")["appnews"]["newsitems"]
    return next(p for p in posts if p["title"].startswith(prefix))


def test_gameplay_update_scores_high():
    post = post_by_prefix("Gameplay Update - 05-22-2026")
    change_lines, score = score_post(post["title"], post["contents"])
    assert change_lines == 307
    assert score >= 100


def test_minor_update_scores_low():
    post = post_by_prefix("Minor Update - 06-04-2026")
    change_lines, score = score_post(post["title"], post["contents"])
    assert change_lines == 14
    assert score < 100


def test_hero_release_scores_high_despite_zero_change_lines():
    post = post_by_prefix("Apollo")
    change_lines, score = score_post(post["title"], post["contents"])
    assert change_lines == 0
    assert score >= 100


def make_client():
    client = FakeClient()
    client.add("GetNewsForApp", (200, {}, fixture_text("steam_news.json")))
    return client


def test_detection_flags_majors_skips_minors_and_foreign_feeds(db):
    client = make_client()
    inserted = detect_era_candidates(db, client, now=ManualNow())

    rows = db.execute("SELECT * FROM era_candidates ORDER BY post_url").fetchall()
    titles = {r["post_title"] for r in rows}
    assert inserted == 2
    assert any(t.startswith("Gameplay Update - 05-22-2026") for t in titles)
    assert any(t.startswith("Apollo") for t in titles)
    # The minor update and the PC Gamer article (wrong feed) are not flagged.
    assert not any("Minor Update" in t for t in titles)
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "pending"
        assert row["posted_at"] is not None
        assert row["change_lines"] is not None
        assert row["score"] >= 100


def test_detection_is_idempotent(db):
    client = make_client()
    assert detect_era_candidates(db, client, now=ManualNow()) == 2
    assert detect_era_candidates(db, client, now=ManualNow()) == 0
    assert db.execute("SELECT COUNT(*) FROM era_candidates").fetchone()[0] == 2


def test_news_response_archived_raw(db):
    client = make_client()
    detect_era_candidates(db, client, now=ManualNow())
    archived = db.execute(
        "SELECT COUNT(*) FROM raw_api_responses WHERE url LIKE '%GetNewsForApp%'"
    ).fetchone()[0]
    assert archived == 1
