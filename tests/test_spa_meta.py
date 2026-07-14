"""Link-preview og/twitter meta injection (api.meta.render_index) and the SPA
fallback route that serves it.

Unit tests exercise render_index directly (no route, no dist). Integration tests
register the real _register_spa on a throwaway app pointed at a temp dist, so the
production route function is under test and a `curl` sees the tags in the raw
HTML body.
"""
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import app as app_module
from api import meta
from tests.conftest import ME

# A minimal shell mirroring frontend/index.html's marker block.
SHELL = """<!doctype html>
<html><head>
<title>Deadlock Stat Tracker</title>
<!-- seo:start -->
<meta property="og:title" content="Deadlock Stat Tracker" />
<meta property="og:description" content="Statistically honest Deadlock performance analytics." />
<!-- seo:end -->
</head><body><div id="root"></div></body></html>"""


def _og_title(html: str) -> str | None:
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    return m.group(1) if m else None


# ── render_index unit tests ──────────────────────────────────────────────────
def test_generic_block_on_root(api_db):
    out = meta.render_index(SHELL, "", api_db)
    assert _og_title(out) == "Deadlock Stat Tracker"
    assert "og:site_name" in out and "twitter:card" in out


def test_personalized_title_for_player_path(api_db):
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert _og_title(out) == f"{ME} — Deadlock Stat Tracker"


def _seed_rank(api_db):
    """Give ME a current rank of Archon 2 (badge 52 = tier 5, subtier 2)."""
    api_db.execute("INSERT INTO ranks(tier, name, color, fetched_at)"
                   " VALUES (5, 'Archon', '#abc', '2026-06-15T12:00:00+00:00')")
    api_db.execute(
        "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
        " VALUES (?, 900, 52, '2026-06-10T00:00:00+00:00')", (ME,))
    api_db.commit()


def test_personalized_title_includes_rank(api_db):
    _seed_rank(api_db)
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert "Archon 2" in out


def test_ranked_player_gets_badge_og_image(api_db):
    _seed_rank(api_db)
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert ('property="og:image" content="https://assets-bucket.deadlock-api.com'
            '/assets-api-res/images/ranks/rank5/badge_lg.png"') in out
    assert 'name="twitter:card" content="summary_large_image"' in out


def test_unranked_player_omits_og_image(api_db):
    # Account has data but no rank series: no og:image tag at all (an empty
    # content="" would render as a broken preview image), plain summary card.
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert "og:image" not in out
    assert 'name="twitter:card" content="summary"' in out


def test_generic_block_og_image_follows_static_constant(api_db, monkeypatch):
    assert "og:image" not in meta.render_index(SHELL, "", api_db)
    monkeypatch.setattr(meta, "STATIC_OG_IMAGE", "https://cdn.example.com/banner.png")
    out = meta.render_index(SHELL, "", api_db)
    assert 'property="og:image" content="https://cdn.example.com/banner.png"' in out
    assert 'name="twitter:card" content="summary_large_image"' in out


def test_player_title_is_label_free(api_db):
    # A private label must not leak into the preview tags.
    api_db.execute("INSERT INTO account_labels(user_id, account_id, display_name)"
                   " VALUES (1, ?, 'SecretNickname')", (ME,))
    api_db.commit()
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert "SecretNickname" not in out


def test_unknown_player_falls_back_to_generic(api_db):
    out = meta.render_index(SHELL, "player/424242", api_db)
    assert _og_title(out) == "Deadlock Stat Tracker"


def test_persona_is_html_escaped(api_db):
    api_db.execute(
        "INSERT INTO steam_personas(account_id, persona_name, fetched_at)"
        " VALUES (?, '<b>x</b>&\"', '2026-06-15T12:00:00+00:00')", (ME,))
    api_db.commit()
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert "<b>x</b>" not in out
    assert "&lt;b&gt;" in out


def test_missing_markers_returns_html_unchanged(api_db):
    plain = "<html><head><title>x</title></head></html>"
    assert meta.render_index(plain, f"player/{ME}", api_db) == plain


def test_personalization_error_falls_back_to_generic(api_db, monkeypatch):
    def boom(conn, account_id):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(meta.service, "player_profile", boom)
    out = meta.render_index(SHELL, f"player/{ME}", api_db)
    assert _og_title(out) == "Deadlock Stat Tracker"


def test_og_url_present_only_with_base_url(api_db, monkeypatch):
    assert "og:url" not in meta.render_index(SHELL, "", api_db)
    monkeypatch.setenv("DEADLOCK_BASE_URL", "https://stats.example.com")
    out = meta.render_index(SHELL, "heroes", api_db)
    assert 'property="og:url" content="https://stats.example.com/heroes"' in out


# ── integration: _register_spa on a temp dist ────────────────────────────────
@pytest.fixture
def spa_client(tmp_path, api_db):
    """A throwaway FastAPI app with only the SPA fallback registered on a temp
    dist. api_db already points DEADLOCK_DB at the seeded DB, and get_conn reads
    it per request, so the profile lookup resolves against real data."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(SHELL, encoding="utf-8")
    (dist / "favicon.ico").write_text("icon", encoding="utf-8")
    test_app = FastAPI()
    app_module._register_spa(test_app, dist)
    return TestClient(test_app)


def test_curl_player_link_has_personalized_title(spa_client):
    # The acceptance criterion: raw HTML body's og:title contains the name.
    body = spa_client.get(f"/player/{ME}").text
    assert _og_title(body) == f"{ME} — Deadlock Stat Tracker"


def test_curl_ranked_player_link_has_og_image(spa_client, api_db):
    # Acceptance criterion: a curl on /player/<id> with a known rank sees the
    # badge art as og:image in the raw HTML.
    _seed_rank(api_db)
    body = spa_client.get(f"/player/{ME}").text
    assert ('property="og:image" content="https://assets-bucket.deadlock-api.com'
            '/assets-api-res/images/ranks/rank5/badge_lg.png"') in body


def test_root_serves_generic_shell(spa_client):
    body = spa_client.get("/").text
    assert _og_title(body) == "Deadlock Stat Tracker"


def test_real_file_is_served(spa_client):
    assert spa_client.get("/favicon.ico").text == "icon"


def test_deep_link_serves_shell(spa_client):
    # An unknown top-level path returns the shell (React Router takes over).
    assert "<div id=\"root\">" in spa_client.get("/improvement").text


def test_unknown_api_path_404s(spa_client):
    assert spa_client.get("/api/does-not-exist").status_code == 404
