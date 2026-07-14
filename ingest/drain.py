"""Loop 2: drain. Fetch full metadata for queued matches, rate-limited.

Failure-kind rules (docs/ingestion-spec.md, as amended) decide what touches a
match's attempt budget:
  - the match's FAULT (a real 4xx like a malformed/forbidden request) counts
    against the 5-attempt budget;
  - our fault (429) and the world's fault (5xx, timeouts, network blips) never
    do -- conflating them is the classic bug where a flaky 3 a.m. network
    permanently marks good matches 'unavailable';
  - the match's SITUATION -- deadlock-api has no replay salts for it, an HTTP
    400 "Match salts ... cannot be fetched" (docs/api-findings.md, verified
    2026-06-19) -- is NOT a fault. Either the salts haven't been harvested yet
    (recoverable soon) or Steam no longer serves them for an old match; the 400
    can't tell which, and during a bulk import many are just transient fallout
    from the 10-req/30-min Steam-salts rate limit. We DEFER: patient hourly
    retries (well under that limit) OUTSIDE the attempt budget, lower priority
    than fresh work, given up only after MAX_DEFER_AGE_S. So recoverable matches
    recover on a later unhurried retry, the rest age out, and a big import can't
    burn good matches' budgets nor starve current ingestion.

One match = one transaction (hard rule 4): the match/players/purchases
inserts and the queue-status flip commit together, so a crash can't leave
them out of step.
"""
import json
import logging
import random
import sqlite3

from ingest.client import BASE_URL, NetworkError, archive_response
from ingest.maintenance import GAVE_UP_ERROR, refresh_assets
from ingest.parse import era_id_for, insert_match, parse_metadata
from ingest.util import iso_to_unix, unix_to_iso, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 600          # 10 min
BACKOFF_CAP_S = 86_400        # 24 h
BACKOFF_JITTER_S = 30
TRANSIENT_RETRY_S = 300       # flat 5 min retry for 5xx / timeout / network
DEFAULT_RATE_LIMIT_SLEEP_S = 300
STRIKE_LIMIT = 5              # consecutive transient strikes before pausing
STRIKE_PAUSE_S = 900          # 15 min circuit-breaker pause
# Not-yet-parsed deferral. 1 h is slow enough to stay background (a match parsed
# mid-day is still picked up the same day) without spending the shared 1-req/5-s
# budget that fresh discovery + drain need. After 14 days an un-parsed match is
# almost certainly deleted/private/purged, so we stop deferring and give up.
DEFERRED_RETRY_S = 3_600
MAX_DEFER_AGE_S = 14 * 24 * 3_600


def _metadata_url(match_id: int) -> str:
    return f"{BASE_URL}/v1/matches/{match_id}/metadata"


def _is_not_parsed(status: int, body: str) -> bool:
    """deadlock-api signals "I have no data for this match" with HTTP 400 and a
    body mentioning replay "salts" (docs/api-findings.md). This is the match's
    situation, not its fault -- distinguished from a genuine 400 by the body."""
    return status == 400 and "salts" in body.lower()


class DrainWorker:
    def __init__(self, conn: sqlite3.Connection, client, *, now=utcnow,
                 sleep=None, rng=random.uniform):
        self.conn = conn
        self.client = client
        self._now = now
        self._sleep = sleep if sleep is not None else __import__("time").sleep
        self._rng = rng
        # transient_strikes is deliberately in-memory, NOT in the database:
        # it's pacing, not progress. Losing it on a crash costs at most a few
        # extra probes before the breaker re-trips, so it is a justified
        # exception to the all-state-in-the-database rule.
        self.transient_strikes = 0
        # assets_refreshed is the same kind of in-memory pacing flag: it caps the
        # unknown-hero recovery to one asset refresh per daemon run (a patch-day
        # burst of new-hero matches must not fire one refresh each). Losing it on
        # a crash costs at most one extra refresh call, so it is the same
        # justified exception to the all-state-in-the-database rule.
        self.assets_refreshed = False

    # ── eligible-row selection ──────────────────────────────────────────────
    #
    # Four tiers, first non-empty tier wins (docs/ingestion-spec.md):
    #   (a) priority > 0 pending / due-failed  -- fair across accounts, newest first
    #   (b) priority = 0 pending / due-failed  -- FIFO by discovery, newest on ties
    #   (c) 'backfill'                         -- fair across accounts, newest first
    #   (d) 'deferred' and due                 -- unchanged, always last
    # Newest-first matters twice over: recent matches are what a user opens the
    # app for, and old ones are the likeliest to 400 on missing replay salts.

    # A row a drain step may act on: pending, or failed with its retry due.
    _FRESH_ELIGIBLE = "(status = 'pending' OR (status = 'failed' AND next_retry_at <= ?))"

    def _next_row(self):
        now_iso = self._now().isoformat()
        row = self._fair_pick(f"{self._FRESH_ELIGIBLE} AND priority > 0", (now_iso,))
        if row is not None:
            return row
        row = self.conn.execute(
            f"SELECT * FROM fetch_queue"
            f" WHERE {self._FRESH_ELIGIBLE} AND priority = 0"
            f" ORDER BY discovered_at, match_id DESC"
            f" LIMIT 1",
            (now_iso,),
        ).fetchone()
        if row is not None:
            return row
        row = self._fair_pick("status = 'backfill'", ())
        if row is not None:
            return row
        # Only when nothing fresh is eligible do we touch deferred work, so a
        # large un-parsed backfill can never starve current ingestion.
        return self.conn.execute(
            "SELECT * FROM fetch_queue"
            " WHERE status = 'deferred' AND next_retry_at <= ?"
            " ORDER BY next_retry_at, match_id"
            " LIMIT 1",
            (now_iso,),
        ).fetchone()

    def _fair_pick(self, where: str, params: tuple):
        """Round-robin fairness: among the accounts owning rows matching `where`,
        pick the one least recently served (sync_state.last_drained_at; NULL --
        never served -- sorts first, which is SQLite's default ASC NULL order),
        then take that account's NEWEST eligible match. So two simultaneous bulk
        imports interleave instead of the first starving the second. Legacy rows
        with no owner form their own NULL group and are handled the same way."""
        account = self.conn.execute(
            f"SELECT fq.discovered_for_account AS account"
            f" FROM fetch_queue fq"
            f" LEFT JOIN sync_state ss ON ss.account_id = fq.discovered_for_account"
            f" WHERE {where}"
            f" GROUP BY fq.discovered_for_account"
            f" ORDER BY ss.last_drained_at, fq.discovered_for_account"
            f" LIMIT 1",
            params,
        ).fetchone()
        if account is None:
            return None
        # `IS ?` (not `= ?`) so the ownerless NULL group matches its rows too.
        return self.conn.execute(
            f"SELECT * FROM fetch_queue"
            f" WHERE {where} AND discovered_for_account IS ?"
            f" ORDER BY match_id DESC"
            f" LIMIT 1",
            (*params, account["account"]),
        ).fetchone()

    # ── one step of work ────────────────────────────────────────────────────

    def step(self) -> str | None:
        """Process one eligible match. Returns an outcome label, or None if
        the queue currently has nothing eligible."""
        row = self._next_row()
        if row is None:
            return None
        outcome = self._fetch_and_handle(row)
        self._stamp_last_drained(row)
        return outcome

    def _stamp_last_drained(self, row) -> None:
        """Advance the owning account's round-robin cursor. Stamped after EVERY
        fetch attempt, success or not: fairness meters the shared API budget,
        and a 404 or 429 spent a request just like a 200 did."""
        account_id = row["discovered_for_account"]
        if account_id is None:
            return
        self.conn.execute(
            "UPDATE sync_state SET last_drained_at = ? WHERE account_id = ?",
            (self._now().isoformat(), account_id),
        )
        self.conn.commit()

    def _fetch_and_handle(self, row) -> str:
        match_id = row["match_id"]
        url = _metadata_url(match_id)
        fetched_at = self._now().isoformat()

        try:
            status, headers, body = self.client.get(url)
        except NetworkError as e:
            log.warning("match %s: network error (%s) -> transient", match_id, e)
            return self._handle_transient(match_id, fetched_at)

        # Hard rule 2 (as amended): archive the raw body before parsing -- EXCEPT a
        # successful metadata 200, whose body is archived by matches.raw_json when
        # _handle_success stores the match (no duplicate). A 200 that FAILS to parse
        # never reaches matches, so _handle_success archives it itself on that path.
        if status != 200:
            archive_response(self.conn, url, status, body, fetched_at)

        if status == 200:
            return self._handle_success(row, url, body, fetched_at)
        if status == 429:
            return self._handle_rate_limited(match_id, headers)
        if status >= 500:
            log.warning("match %s: HTTP %s -> transient", match_id, status)
            return self._handle_transient(match_id, fetched_at)
        if _is_not_parsed(status, body):
            return self._handle_not_parsed(row, fetched_at)
        # Other 4xx (a genuine 404/403/malformed 400): the match's fault.
        return self._handle_match_fault(row, status, fetched_at)

    # ── outcome handlers ────────────────────────────────────────────────────

    def _handle_success(self, row, url: str, body: str, fetched_at: str) -> str:
        # HTTP 200 only means the request succeeded, NOT that the body is usable
        # metadata: an empty, truncated, or unexpected body makes json.loads /
        # parse_metadata / insert_match raise. Unguarded, that exception would
        # propagate out of the drain loop and kill the daemon while this row stays
        # 'pending' -- a restart refetches the same match and crashes again, so one
        # poison message halts ingestion for everyone. We treat a bad payload as
        # the match's FAULT and spend the attempt budget like a 404.
        #
        # A good 200's archive IS matches.raw_json (written by insert_match), so we
        # skip the duplicate raw_api_responses insert on the success path. But a bad
        # payload never reaches matches, so on the except path below we archive the
        # raw body into raw_api_responses for forensics before marking the fault --
        # nothing fetched is ever lost.
        match_id = row["match_id"]
        try:
            meta = json.loads(body)
            start_time_iso = unix_to_iso(meta["match_info"]["start_time"])
            era_id = era_id_for(self.conn, start_time_iso)
            shop_item_ids = {r["item_id"] for r in
                             self.conn.execute("SELECT item_id FROM items").fetchall()}
            ability_item_ids = {r["ability_id"] for r in
                                self.conn.execute("SELECT ability_id FROM abilities").fetchall()}
            parsed = parse_metadata(meta, body, shop_item_ids, era_id, fetched_at,
                                    ability_item_ids)
            unknown = self._unknown_hero_ids(parsed)

            # One transaction: the match rows, any hero placeholders, and the queue
            # flip commit together (hard rule 4).
            with self.conn:
                self._insert_hero_placeholders(unknown, fetched_at)
                insert_match(self.conn, parsed)
                self.conn.execute(
                    "UPDATE fetch_queue SET status = 'fetched', last_attempt_at = ?,"
                    " next_retry_at = NULL, last_error = NULL WHERE match_id = ?",
                    (fetched_at, match_id),
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                sqlite3.IntegrityError) as exc:
            # `with self.conn` already rolled back its own writes on the exception;
            # rollback() again is a safe no-op that also covers a failure before the
            # block opened. The match never made it into matches, so archive the raw
            # body into raw_api_responses now (hard rule 2) -- this is the one place a
            # 200 gets archived there, and reprocess-archive recovers from it later.
            self.conn.rollback()
            archive_response(self.conn, url, 200, body, fetched_at)
            error = f"bad payload: {type(exc).__name__}: {str(exc)[:200]}"
            log.warning("match %s: %s -> fault", match_id, error)
            return self._mark_match_fault(row, error, fetched_at)

        self.transient_strikes = 0  # any successful ingest resets the streak
        log.info("match %s pending -> fetched", match_id)
        return "fetched"

    def _unknown_hero_ids(self, parsed) -> set[int]:
        """The hero_ids in this match that are NOT in the heroes table. A hero
        released after the last nightly asset refresh would otherwise fail the
        match_players FK insert with IntegrityError -- the same crash loop as a
        poison payload, but on patch days. We refresh assets ONCE per daemon run to
        try to learn the hero; whatever stays unknown gets a placeholder row so the
        FK holds (the nightly refresh fills the real name later)."""
        hero_ids = {p["hero_id"] for p in parsed.players}
        unknown = self._missing_heroes(hero_ids)
        if unknown and not self.assets_refreshed:
            # One refresh attempt per daemon run, regardless of outcome, so a burst
            # of new-hero matches can't fire a refresh each. A network blip during
            # the refresh just falls through to the placeholder path.
            self.assets_refreshed = True
            try:
                refresh_assets(self.conn, self.client, now=self._now)
            except NetworkError as e:
                log.warning("unknown-hero asset refresh failed (%s); using placeholders", e)
            unknown = self._missing_heroes(hero_ids)
        return unknown

    def _missing_heroes(self, hero_ids: set[int]) -> set[int]:
        if not hero_ids:
            return set()
        placeholders = ",".join("?" for _ in hero_ids)
        known = {r["hero_id"] for r in self.conn.execute(
            f"SELECT hero_id FROM heroes WHERE hero_id IN ({placeholders})",
            tuple(hero_ids))}
        return hero_ids - known

    def _insert_hero_placeholders(self, hero_ids: set[int], fetched_at: str) -> None:
        """Write a placeholder heroes row for each still-unknown hero so the
        match_players FK holds. INSERT OR IGNORE: a concurrent placeholder or a
        hero the refresh just learned is harmless. Caller owns the transaction."""
        for hero_id in hero_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO heroes(hero_id, name, image_url, fetched_at)"
                " VALUES (?, ?, NULL, ?)",
                (hero_id, f"Unknown hero {hero_id}", fetched_at),
            )

    def _handle_match_fault(self, row, status: int, fetched_at: str) -> str:
        return self._mark_match_fault(row, f"HTTP {status}", fetched_at)

    def _mark_match_fault(self, row, error: str, fetched_at: str) -> str:
        """Spend one attempt on a match-fault outcome (a 404, a genuine 4xx, or a
        bad payload). Gives up to 'unavailable' at MAX_ATTEMPTS, else 'failed' with
        exponential backoff. Shared by every fault path so they age out identically."""
        match_id = row["match_id"]
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            self.conn.execute(
                "UPDATE fetch_queue SET status = 'unavailable', attempts = ?,"
                " last_attempt_at = ?, last_error = ? WHERE match_id = ?",
                (attempts, fetched_at, error, match_id),
            )
            self.conn.commit()
            log.info("match %s -> unavailable (gave up after %d attempts)", match_id, attempts)
            return "unavailable"

        next_retry = self._backoff_iso(attempts)
        self.conn.execute(
            "UPDATE fetch_queue SET status = 'failed', attempts = ?, last_attempt_at = ?,"
            " next_retry_at = ?, last_error = ? WHERE match_id = ?",
            (attempts, fetched_at, next_retry, error, match_id),
        )
        self.conn.commit()
        log.info("match %s -> failed (attempt %d, retry at %s)", match_id, attempts, next_retry)
        return "failed"

    def _handle_not_parsed(self, row, fetched_at: str) -> str:
        # Not the match's fault: deadlock-api just has no replay salts for it
        # (yet, or ever). Defer patiently -- never touch attempts or the circuit
        # breaker -- but stop after MAX_DEFER_AGE_S so a permanently dead match
        # can't loop on the patient path forever. deferred_since is set ONCE (the
        # first deferral), so the give-up clock measures total time deferred.
        match_id = row["match_id"]
        deferred_since = row["deferred_since"] or fetched_at
        if iso_to_unix(fetched_at) - iso_to_unix(deferred_since) >= MAX_DEFER_AGE_S:
            self.conn.execute(
                "UPDATE fetch_queue SET status = 'unavailable', deferred_since = ?,"
                " last_attempt_at = ?, last_error = ?"
                " WHERE match_id = ?",
                (deferred_since, fetched_at, GAVE_UP_ERROR, match_id),
            )
            self.conn.commit()
            log.info("match %s -> unavailable (deferred past max age)", match_id)
            return "unavailable"

        next_retry = self._iso_after(DEFERRED_RETRY_S)
        self.conn.execute(
            "UPDATE fetch_queue SET status = 'deferred', deferred_since = ?,"
            " last_attempt_at = ?, next_retry_at = ?, last_error = 'not parsed'"
            " WHERE match_id = ?",
            (deferred_since, fetched_at, next_retry, match_id),
        )
        self.conn.commit()
        log.info("match %s -> deferred (not parsed yet, retry at %s)", match_id, next_retry)
        return "deferred"

    def _handle_rate_limited(self, match_id: int, headers: dict) -> str:
        # 429 is our fault, global, not this match's: leave the row entirely
        # untouched (status, attempts, next_retry_at) and just slow down.
        retry_after = headers.get("Retry-After")
        try:
            sleep_s = float(retry_after) if retry_after else DEFAULT_RATE_LIMIT_SLEEP_S
        except ValueError:
            sleep_s = DEFAULT_RATE_LIMIT_SLEEP_S
        log.warning("match %s: HTTP 429, sleeping %.0fs (row untouched)", match_id, sleep_s)
        self._sleep(sleep_s)
        return "rate_limited"

    def _handle_transient(self, match_id: int, fetched_at: str) -> str:
        # 5xx / timeout / network: never touch attempts. Flat 5-min retry.
        next_retry = self._iso_after(TRANSIENT_RETRY_S)
        self.conn.execute(
            "UPDATE fetch_queue SET status = 'failed', last_attempt_at = ?,"
            " next_retry_at = ?, last_error = 'transient' WHERE match_id = ?",
            (fetched_at, next_retry, match_id),
        )
        self.conn.commit()

        self.transient_strikes += 1
        log.info("match %s -> failed (transient, strike %d/%d, attempts unchanged)",
                 match_id, self.transient_strikes, STRIKE_LIMIT)
        if self.transient_strikes >= STRIKE_LIMIT:
            log.warning("circuit breaker: %d consecutive transient failures, pausing %ds",
                        self.transient_strikes, STRIKE_PAUSE_S)
            self._sleep(STRIKE_PAUSE_S)
            self.transient_strikes = 0
        return "transient"

    # ── drain until nothing is eligible ─────────────────────────────────────

    def drain(self, *, max_steps: int | None = None) -> int:
        """Process eligible matches until none remain (or max_steps reached).
        Returns the number of steps taken."""
        steps = 0
        while max_steps is None or steps < max_steps:
            outcome = self.step()
            if outcome is None:
                break
            steps += 1
        return steps

    # ── timing helpers ──────────────────────────────────────────────────────

    def _backoff_iso(self, attempts: int) -> str:
        base = min(BACKOFF_BASE_S * (2 ** attempts), BACKOFF_CAP_S)
        return self._iso_after(base + self._rng(0, BACKOFF_JITTER_S))

    def _iso_after(self, seconds: float) -> str:
        from datetime import timedelta
        return (self._now() + timedelta(seconds=seconds)).isoformat()
