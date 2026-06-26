"""FastAPI app: the web face of api.service.

Each endpoint is a thin wrapper -- parse the scope query params, open a
read-only connection, and hand off to api.service, which owns all SQL and all
statistics. The frontend renders what these return and computes nothing.
"""
import secrets
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import auth, service
from api.config import auth_enabled, base_url, db_path
from ingest.util import DEFAULT_USER_ID
from stats.trends import TRENDS_WINDOW_DEFAULT
from api.scope import (
    DEFAULT_MIN_GAMES,
    FULL_BADGE_MAX,
    FULL_BADGE_MIN,
    GAME_MODE_NORMAL,
    Scope,
    make_scope,
)
from tracker.db import connect

app = FastAPI(title="Deadlock Stat Tracker API", version="1.0")


def get_conn():
    """A fresh read connection per request, closed when the request ends.

    check_same_thread=False because FastAPI may run the dependency and the path
    function in different threadpool workers; each connection still serves one
    request at a time (see tracker.db.connect)."""
    conn = connect(db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


SESSION_COOKIE = "session"
CSRF_COOKIE = "csrf"


def _resolve_user(request: Request, conn: sqlite3.Connection) -> int | None:
    """The current user id from the session cookie, or None if not logged in.
    In local/dev mode (auth off) there is no login, so it's always the default
    user -- the single-user workflow is unchanged."""
    if not auth_enabled():
        return DEFAULT_USER_ID
    return auth.user_for_session(conn, request.cookies.get(SESSION_COOKIE))


def get_optional_user_id(request: Request,
                         conn: sqlite3.Connection = Depends(get_conn)) -> int | None:
    """Read dependency: the current user, or None when auth is on and nobody is
    logged in. Reads tolerate None (they show an empty/anonymous state)."""
    return _resolve_user(request, conn)


def require_user(request: Request,
                 conn: sqlite3.Connection = Depends(get_conn)) -> int:
    """Write gate: a real login (401 otherwise) plus, in auth mode, a valid CSRF
    double-submit token (403 otherwise). Returns the user id the write is scoped
    to, so a user can only ever write within their own identity. Replaces the old
    deploy-time owner flag.

    Double-submit CSRF: login sets a non-httpOnly `csrf` cookie; the SPA echoes it
    in the X-CSRF-Token header. A forged cross-site request can send the cookie but
    can't read it to set the header, so the equality check fails."""
    user_id = _resolve_user(request, conn)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Login required.")
    if auth_enabled():
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get("X-CSRF-Token")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    return user_id


def get_scope(
    account_id: int | None = None,
    era_ids: str | None = None,
    badge_min: int = FULL_BADGE_MIN,
    badge_max: int = FULL_BADGE_MAX,
    min_games: int = DEFAULT_MIN_GAMES,
    game_mode: str = GAME_MODE_NORMAL,
    in_lane: bool = False,
) -> Scope:
    """Build the shared Scope from the standard query-string params."""
    return make_scope(account_id, era_ids, badge_min, badge_max, min_games,
                      game_mode, in_lane)


@app.get("/api/matchups")
def get_matchups(hero_id: int | None = None, scope: Scope = Depends(get_scope),
                 conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.matchups(conn, scope, hero_id=hero_id)


@app.get("/api/items")
def get_items(hero_id: int, scope: Scope = Depends(get_scope),
              conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.items(conn, scope, hero_id)


@app.get("/api/heroes")
def get_heroes(scope: Scope = Depends(get_scope),
               conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.played_heroes(conn, scope)


@app.get("/api/performance")
def get_performance(scope: Scope = Depends(get_scope),
                    conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    """Continuous-metric performance (net worth/min, KDA, damage, ...) per hero
    and overall, each vs a live population baseline at this scope."""
    return service.performance(conn, scope)


@app.get("/api/laning")
def get_laning(scope: Scope = Depends(get_scope),
               conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    """Early-game (laning) report: net worth, last hits, and denies at the
    lane-end mark per hero and overall, each vs the live population at this scope."""
    return service.laning(conn, scope)


@app.get("/api/trends")
def get_trends(mode: str = "rolling", granularity: str = "week",
               window_games: int = TRENDS_WINDOW_DEFAULT, hero_id: int | None = None,
               scope: Scope = Depends(get_scope),
               conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Performance over time: win rate + continuous metrics as a chronological
    series, either a rolling moving average (`mode=rolling`, `window_games=N`) or
    calendar buckets (`mode=calendar`, `granularity=week|month`). `hero_id`
    re-bases the whole screen to matches on that hero, like matchups."""
    return service.trends(conn, scope, mode=mode, granularity=granularity,
                          window_games=window_games, hero_id=hero_id)


@app.get("/api/death-patterns")
def get_death_patterns(scope: Scope = Depends(get_scope),
                       conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Across the scoped match set: which enemy heroes kill you most (raw deaths +
    games faced, no verdict) and how your deaths distribute over the game timeline
    (game-minute bins vs a live population baseline, lower-is-better verdicts)."""
    return service.death_patterns(conn, scope)


@app.get("/api/ranks")
def get_ranks(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.ranks(conn)


@app.get("/api/accounts")
def get_accounts(user_id: int | None = Depends(get_optional_user_id),
                 conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    """The viewer's account switcher. Scoped to the logged-in user; empty when auth
    is on and nobody is logged in."""
    if user_id is None:
        return []
    return service.accounts(conn, user_id)


class AddAccountBody(BaseModel):
    # int | str: the identifier may be a raw account id, a 17-digit SteamID64, or
    # a profile URL -- service.add_account normalizes them all (ingest.to_account_id).
    account_id: int | str
    display_name: str | None = None


class SetNameBody(BaseModel):
    display_name: str


@app.post("/api/accounts", status_code=202)
def post_account(body: AddAccountBody,
                 user_id: int = Depends(require_user),
                 conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Claim/import a tracked account for the logged-in user. 202 Accepted, not
    200/201: this only records the account (the enqueue); the worker ingests its
    matches on a later cycle, so we never block on the rate-limited API. The account
    is linked to require_user's user_id, so a user only ever adds to their own."""
    try:
        return service.add_account(conn, body.account_id, body.display_name,
                                   user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/accounts/{account_id}/name")
def put_account_name(account_id: int, body: SetNameBody,
                     user_id: int = Depends(require_user),
                     conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Set a manual label for any account (the one rename path). Works for untracked
    accounts too -- co-players/opponents are the point. The label is private to
    require_user's user_id, so a user only names within their own identity. Empty
    names are 400; use DELETE to clear."""
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name must not be empty")
    return service.set_account_name(conn, account_id, name, user_id=user_id)


@app.delete("/api/accounts/{account_id}/name")
def delete_account_name(account_id: int,
                        user_id: int = Depends(require_user),
                        conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Clear the logged-in user's manual label for an account, reverting to its
    persona then bare id. Idempotent (no 404 when there was no label)."""
    return service.clear_account_name(conn, account_id, user_id=user_id)


@app.get("/api/improvement")
def get_improvement(hero_id: int | None = None, scope: Scope = Depends(get_scope),
                    conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.improvement(conn, scope, hero_id=hero_id)


@app.get("/api/tilt")
def get_tilt(scope: Scope = Depends(get_scope),
             conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Session-index and loss-streak performance for the scoped account."""
    return service.tilt(conn, scope)


@app.get("/api/recurring-players")
def get_recurring_players(hero_id: int | None = None,
                          scope: Scope = Depends(get_scope),
                          conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Recurring teammates (win rate with) and opponents (win rate against) for
    the scoped account, judged against its own win rate. `hero_id` re-baselines
    the whole screen to matches on that hero, like the matchups perspective."""
    return service.recurring_players(conn, scope, hero_id=hero_id)


@app.get("/api/overview")
def get_overview(scope: Scope = Depends(get_scope),
                 conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.overview(conn, scope)


@app.get("/api/matches/{match_id}")
def get_match_detail(match_id: int, account_id: int | None = None,
                     conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """One match's detail. No Scope: this is single-match data, not an aggregate.
    `account_id` is the optional "you" perspective (defaults to the self account)."""
    result = service.match_detail(conn, match_id, account_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return result


@app.get("/api/sync-status")
def get_sync_status(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.sync_status(conn)


@app.get("/api/eras")
def get_eras(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.eras(conn)


# Era management edits global data (the patch_era timeline), so it requires a
# login, not per-user scoping. A dedicated admin role is future work; for now any
# authenticated user may curate eras (require_user), and in local/dev mode it's the
# single default user -- same reach as the rest of the writes.
@app.post("/api/eras/candidates/{candidate_id}/confirm")
def post_confirm_candidate(candidate_id: int,
                           user_id: int = Depends(require_user),
                           conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    result = service.confirm_candidate(conn, candidate_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/eras/candidates/{candidate_id}/dismiss")
def post_dismiss_candidate(candidate_id: int,
                           user_id: int = Depends(require_user),
                           conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    result = service.dismiss_candidate(conn, candidate_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Authentication (Steam OpenID) ────────────────────────────────────────────
# Active only when DEADLOCK_BASE_URL is set (config.auth_enabled). In local/dev
# these are 404s and the app stays single-user.

@app.get("/api/auth/login")
def auth_login() -> RedirectResponse:
    """Send the user to Steam to sign in. Steam returns to /api/auth/callback."""
    base = base_url()
    if base is None:
        raise HTTPException(status_code=404, detail="Authentication is not enabled.")
    return RedirectResponse(
        auth.login_redirect_url(f"{base}/api/auth/callback", f"{base}/")
    )


@app.get("/api/auth/callback")
def auth_callback(request: Request,
                  conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    """Verify Steam's reply, open a session, and set the cookies, then bounce back
    to the app. Sets two cookies: the httpOnly session token and a readable CSRF
    token the SPA echoes on writes."""
    base = base_url()
    if base is None:
        raise HTTPException(status_code=404, detail="Authentication is not enabled.")
    account_id = auth.verify_callback(dict(request.query_params))
    if account_id is None:
        raise HTTPException(status_code=400, detail="Steam login could not be verified.")
    user_id = auth.find_or_create_user(conn, account_id)
    token = auth.create_session(conn, user_id)

    resp = RedirectResponse(f"{base}/", status_code=303)
    secure = base.startswith("https")
    max_age = int(auth.SESSION_TTL.total_seconds())
    resp.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True,
                    secure=secure, samesite="lax")
    resp.set_cookie(CSRF_COOKIE, auth.new_csrf_token(), max_age=max_age,
                    httponly=False, secure=secure, samesite="lax")
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request,
                user_id: int = Depends(require_user),
                conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    """Revoke the session and clear the cookies. CSRF-protected like any write."""
    auth.delete_session(conn, request.cookies.get(SESSION_COOKIE))
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE)
    resp.delete_cookie(CSRF_COOKIE)
    return resp


@app.get("/api/auth/me")
def auth_me(user_id: int | None = Depends(get_optional_user_id),
            conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Who the viewer is: whether auth is even on (auth_enabled), whether they're
    logged in (authenticated), their user id, and their self account + resolved
    name. The frontend uses auth_enabled to decide whether to show login controls
    at all -- in local/dev mode it's the default user, authenticated=False."""
    enabled = auth_enabled()
    if user_id is None:
        return {"auth_enabled": enabled, "authenticated": False,
                "user_id": None, "account_id": None, "display_name": None}
    return {"auth_enabled": enabled, "authenticated": enabled,
            **service.me(conn, user_id)}


# --- Single-origin static serving -----------------------------------------
# In production the API and the built React SPA are served from one origin (see
# frontend/vite.config.ts): every /api/* path above is a real handler, and any
# other path serves the SPA so React Router can take it client-side. This block
# is registered LAST, so the /api routes always win, and is guarded on the build
# existing so the app still imports (and the test suite runs) without a built
# frontend -- e.g. in CI, where frontend/dist is gitignored and absent.
_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    # Hashed JS/CSS bundles. Mounted before the catch-all (so it wins over it)
    # and after every /api route (so it never shadows the API).
    _assets = _DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """Serve a real file from the build if present, else index.html.

        The index.html fallback is what makes deep links work: hitting /trends
        directly returns the app shell, then React Router renders the route.
        """
        # Never answer for the API: an unknown /api/* path must 404 as JSON, not
        # silently return index.html. (Belt-and-suspenders -- the real /api
        # routes are registered earlier and already win.)
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # Serve a real top-level file (favicon, etc.) only if it resolves safely
        # inside the build dir; otherwise hand back the SPA shell.
        candidate = (_DIST / full_path).resolve()
        if full_path and candidate.is_file() and _DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
