"""Steam personas: resolve account_ids to display names + avatars.

Phase 1 of 3 (auto names). Self-contained and optional: with no STEAM_API_KEY set,
refresh_personas is a clean no-op and the rest of the app falls back to bare
account ids (graceful degradation -- a contributor without a key is unaffected).

Steam's quota is generous and independent of the deadlock-api budget, so persona
traffic gets its OWN token bucket (build_steam_client) and never spends the
deadlock 1-req/5s budget. We still stay polite to Steam (1 req/sec, batched 100).

Archive-before-parse (hard rule 2) is honored via archive_response -- but the
Steam URL carries the secret ?key=, so we archive a redacted copy of the URL while
GETting the real one. The response body never contains the key.
"""
import json
import logging
from datetime import timedelta
from pathlib import Path

from api.config import steam_api_key
from ingest.accounts import to_steamid64
from ingest.client import Client, NetworkError, archive_response
from ingest.ratelimit import TokenBucket
from ingest.util import utcnow

log = logging.getLogger(__name__)

SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
REFRESH_AGE_S = 14 * 24 * 3600     # re-fetch a persona once it is 14 days old
MAX_PER_CYCLE = 500                # at most 5 batches of 100 per maintenance run
BATCH_SIZE = 100                   # Steam's documented GetPlayerSummaries limit

# Persona traffic rides its own token bucket + stamp file so it never touches the
# deadlock-api budget. 1 req/sec is plenty polite given one call resolves 100 ids.
STEAM_STAMP = Path(__file__).parent.parent / "data" / ".last_steam_request"

_UPSERT = (
    "INSERT INTO steam_personas(account_id, persona_name, avatar_url, fetched_at)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT(account_id) DO UPDATE SET"
    "   persona_name = excluded.persona_name,"
    "   avatar_url   = excluded.avatar_url,"
    "   fetched_at   = excluded.fetched_at"
)


def build_steam_client() -> Client:
    """A Client on a SEPARATE token bucket so Steam never spends the deadlock budget."""
    return Client(TokenBucket(rate=1.0, capacity=1.0, stamp_path=STEAM_STAMP))


def _due_account_ids(conn, cutoff: str) -> list[int]:
    """account_ids in match_players that are missing a persona or older than the
    cutoff. Positive only (account_id <= 0 is an anonymized player with no Steam
    id), missing first then oldest, capped at MAX_PER_CYCLE."""
    rows = conn.execute(
        "SELECT account_id FROM ("
        "  SELECT DISTINCT mp.account_id AS account_id, sp.fetched_at AS fetched_at"
        "    FROM match_players mp"
        "    LEFT JOIN steam_personas sp ON sp.account_id = mp.account_id"
        "   WHERE mp.account_id > 0"
        "     AND (sp.account_id IS NULL OR sp.fetched_at < ?)"
        ")"
        " ORDER BY (fetched_at IS NOT NULL), fetched_at"
        " LIMIT ?",
        (cutoff, MAX_PER_CYCLE),
    ).fetchall()
    return [r["account_id"] for r in rows]


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def refresh_personas(conn, client, *, now=utcnow) -> int:
    """Resolve due account_ids to Steam personas, in batches of 100. Returns the
    count of personas resolved (rows that got a real name). Clean no-op with no key."""
    key = steam_api_key()
    if not key:
        log.debug("personas: STEAM_API_KEY not set; skipping")
        return 0

    fetched_at = now().isoformat()
    cutoff = (now() - timedelta(seconds=REFRESH_AGE_S)).isoformat()
    due = _due_account_ids(conn, cutoff)
    if not due:
        return 0

    resolved = 0
    for batch in _chunks(due, BATCH_SIZE):
        ids_csv = ",".join(str(to_steamid64(a)) for a in batch)
        real_url = f"{SUMMARIES_URL}?key={key}&steamids={ids_csv}"
        safe_url = f"{SUMMARIES_URL}?key=***&steamids={ids_csv}"   # never archive the key
        try:
            status, _headers, body = client.get(real_url)
        except NetworkError as exc:
            # Steam is a secondary enrichment API; a blip must not abort the
            # nightly deadlock maintenance. Stop this cycle, retry next run.
            log.warning("personas: network error, stopping cycle (%s)", exc)
            break
        archive_response(conn, safe_url, status, body, fetched_at)
        if status != 200:
            log.warning("personas: GetPlayerSummaries HTTP %s; %d account(s) stay due",
                        status, len(batch))
            continue
        if not body or not body.strip():
            log.warning("personas: empty 200 body; %d account(s) stay due", len(batch))
            continue

        players = json.loads(body).get("response", {}).get("players", [])
        by_steamid = {int(p["steamid"]): p for p in players}
        rows = []
        for account_id in batch:
            player = by_steamid.get(to_steamid64(account_id))
            if player is not None:
                rows.append((account_id, player.get("personaname"),
                             player.get("avatarfull"), fetched_at))
                resolved += 1
            else:
                # Private / unresolved: write a NULL-name placeholder so it ages
                # out of the due query instead of being re-queried every night.
                rows.append((account_id, None, None, fetched_at))
        conn.executemany(_UPSERT, rows)
        conn.commit()

    log.info("personas: resolved %d of %d due account(s)", resolved, len(due))
    return resolved
