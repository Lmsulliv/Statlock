"""Server-side link-preview (Open Graph / Twitter card) injection for the SPA.

The built index.html carries a placeholder block between `<!-- seo:start -->` and
`<!-- seo:end -->` with generic tags. Per request, `render_index` swaps that block
for tags appropriate to the path so a pasted link unfurls with the right title --
crawlers never run our JS, so the tags must be in the raw HTML.

For a `/player/{id}` path we personalize the title with the account's public
(label-free) display name and current rank; every value is HTML-escaped, and any
error falls back to the generic block. Everything here is a pure string transform
plus read-only queries, so it is safe on the anonymous crawler path.
"""
import html
import re

from api import service
from api.config import base_url

SITE_NAME = "Deadlock Stat Tracker"
_TAGLINE = "Statistically honest Deadlock performance analytics."
# Preview image for non-player pages. An operator hosting branding art can point
# this at its absolute URL; None omits og:image entirely (an empty tag would
# unfurl as a broken image).
STATIC_OG_IMAGE: str | None = None

# The placeholder block, markers inclusive. Non-greedy so it stops at the first
# end marker; DOTALL so it spans the newlines between the tags.
_BLOCK_RE = re.compile(r"<!-- seo:start -->.*?<!-- seo:end -->", re.DOTALL)
# A profile path is "player/<digits>" optionally followed by a sub-path.
_PLAYER_RE = re.compile(r"^player/(\d+)(?:/|$)")


def _prop(name: str, value: str) -> str:
    return f'<meta property="{name}" content="{html.escape(value, quote=True)}" />'


def _named(name: str, value: str) -> str:
    return f'<meta name="{name}" content="{html.escape(value, quote=True)}" />'


def _block(title: str, description: str, url: str | None,
           image: str | None = None) -> str:
    """The full marker-wrapped tag block for the given title/description/url.
    With an image, the twitter card upgrades to the large-image layout."""
    tags = [
        _named("description", description),
        _prop("og:title", title),
        _prop("og:description", description),
        _prop("og:site_name", SITE_NAME),
        _prop("og:type", "website"),
        _named("twitter:card", "summary_large_image" if image else "summary"),
        _named("twitter:title", title),
        _named("twitter:description", description),
    ]
    if image:
        tags.append(_prop("og:image", image))
    if url:
        tags.insert(1, _prop("og:url", url))
    body = "\n    ".join(tags)
    return f"<!-- seo:start -->\n    {body}\n    <!-- seo:end -->"


def _canonical_url(path: str) -> str | None:
    """The page's absolute URL from the configured public origin, or None when no
    origin is set (local/dev) -- omitting og:url is better than a wrong one."""
    base = base_url()
    return f"{base}/{path}" if base else None


def _rank_phrase(current_rank: dict | None) -> str:
    """A short human rank label like 'Archon 3', or '' when unknown/unresolvable."""
    if not current_rank or not current_rank.get("name"):
        return ""
    subtier = current_rank.get("subtier") or 0
    return f"{current_rank['name']} {subtier}".strip() if subtier else current_rank["name"]


def _player_block(account_id: int, path: str, conn) -> str:
    """Personalized block for a profile path; generic block when the account has
    no data. Callers wrap this so any failure degrades to generic."""
    profile = service.player_profile(conn, account_id)
    if not profile["has_data"]:
        return _generic_block(path)
    name = profile["display_name"]
    rank = _rank_phrase(profile["current_rank"])
    title = f"{name} — {SITE_NAME}"
    description = (f"{name} · {rank}. {_TAGLINE}" if rank else f"{name}. {_TAGLINE}")
    # The current rank's badge art makes the unfurl recognizable; unranked
    # accounts get no image rather than a placeholder.
    image = (profile["current_rank"] or {}).get("badge_url")
    return _block(title, description, _canonical_url(path), image)


def _generic_block(path: str) -> str:
    return _block(SITE_NAME, _TAGLINE, _canonical_url(path), STATIC_OG_IMAGE)


def render_index(html_text: str, path: str, conn) -> str:
    """Return index.html with its SEO block replaced for `path`.

    `path` is the request path without a leading slash ("" for root,
    "player/900000" for a profile). Missing markers -> the file is returned
    unchanged. Any error while personalizing -> the generic block.
    """
    if not _BLOCK_RE.search(html_text):
        return html_text
    match = _PLAYER_RE.match(path)
    try:
        if match:
            block = _player_block(int(match.group(1)), path, conn)
        else:
            block = _generic_block(path)
    except Exception:
        block = _generic_block(path)
    return _BLOCK_RE.sub(lambda _: block, html_text, count=1)
