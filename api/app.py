"""FastAPI app: the web face of api.service.

Each endpoint is a thin wrapper -- parse the scope query params, open a
read-only connection, and hand off to api.service, which owns all SQL and all
statistics. The frontend renders what these return and computes nothing.
"""
import sqlite3

from fastapi import Depends, FastAPI, HTTPException

from api import service
from api.config import db_path
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


def get_scope(
    account_id: int | None = None,
    era_ids: str | None = None,
    badge_min: int = FULL_BADGE_MIN,
    badge_max: int = FULL_BADGE_MAX,
    min_games: int = DEFAULT_MIN_GAMES,
    game_mode: str = GAME_MODE_NORMAL,
) -> Scope:
    """Build the shared Scope from the standard query-string params."""
    return make_scope(account_id, era_ids, badge_min, badge_max, min_games, game_mode)


@app.get("/api/matchups")
def get_matchups(hero_id: int | None = None, scope: Scope = Depends(get_scope),
                 conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.matchups(conn, scope, hero_id=hero_id)


@app.get("/api/items")
def get_items(hero_id: int, scope: Scope = Depends(get_scope),
              conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return service.items(conn, scope, hero_id)


@app.get("/api/improvement")
def get_improvement(scope: Scope = Depends(get_scope),
                    conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.improvement(conn, scope)


@app.get("/api/overview")
def get_overview(scope: Scope = Depends(get_scope),
                 conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.overview(conn, scope)


@app.get("/api/sync-status")
def get_sync_status(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.sync_status(conn)


@app.get("/api/eras")
def get_eras(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return service.eras(conn)


@app.post("/api/eras/candidates/{candidate_id}/confirm")
def post_confirm_candidate(candidate_id: int,
                           conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    result = service.confirm_candidate(conn, candidate_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/eras/candidates/{candidate_id}/dismiss")
def post_dismiss_candidate(candidate_id: int,
                           conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    result = service.dismiss_candidate(conn, candidate_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
