"""Loop 2: drain. Fetch full metadata for queued matches, rate-limited.

This is where the two failure-kind rules live (docs/ingestion-spec.md, as
amended): only the match's own fault (404 / not-yet-parsed) counts against
its attempt budget; our fault (429) and the world's fault (5xx, timeouts,
network blips) never do. Conflating them is the classic bug where a flaky
3 a.m. network permanently marks good matches 'unavailable'.

One match = one transaction (hard rule 4): the match/players/purchases
inserts and the queue-status flip commit together, so a crash can't leave
them out of step.
"""
import json
import logging
import random
import sqlite3

from ingest.client import BASE_URL, NetworkError, archive_response
from ingest.parse import era_id_for, insert_match, parse_metadata
from ingest.util import unix_to_iso, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 600          # 10 min
BACKOFF_CAP_S = 86_400        # 24 h
BACKOFF_JITTER_S = 30
TRANSIENT_RETRY_S = 300       # flat 5 min retry for 5xx / timeout / network
DEFAULT_RATE_LIMIT_SLEEP_S = 300
STRIKE_LIMIT = 5              # consecutive transient strikes before pausing
STRIKE_PAUSE_S = 900          # 15 min circuit-breaker pause


def _metadata_url(match_id: int) -> str:
    return f"{BASE_URL}/v1/matches/{match_id}/metadata"


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

    # ── eligible-row selection ──────────────────────────────────────────────

    def _next_row(self):
        now_iso = self._now().isoformat()
        return self.conn.execute(
            "SELECT * FROM fetch_queue"
            " WHERE status = 'pending'"
            "    OR (status = 'failed' AND next_retry_at <= ?)"
            " ORDER BY discovered_at, match_id"
            " LIMIT 1",
            (now_iso,),
        ).fetchone()

    # ── one step of work ────────────────────────────────────────────────────

    def step(self) -> str | None:
        """Process one eligible match. Returns an outcome label, or None if
        the queue currently has nothing eligible."""
        row = self._next_row()
        if row is None:
            return None

        match_id = row["match_id"]
        url = _metadata_url(match_id)
        fetched_at = self._now().isoformat()

        try:
            status, headers, body = self.client.get(url)
        except NetworkError as e:
            log.warning("match %s: network error (%s) -> transient", match_id, e)
            return self._handle_transient(match_id, fetched_at)

        # Hard rule 2: archive raw before parsing.
        archive_response(self.conn, url, status, body, fetched_at)

        if status == 200:
            return self._handle_success(row, body, fetched_at)
        if status == 429:
            return self._handle_rate_limited(match_id, headers)
        if status >= 500:
            log.warning("match %s: HTTP %s -> transient", match_id, status)
            return self._handle_transient(match_id, fetched_at)
        # 404 and other 4xx: the match's fault.
        return self._handle_match_fault(row, status, fetched_at)

    # ── outcome handlers ────────────────────────────────────────────────────

    def _handle_success(self, row, body: str, fetched_at: str) -> str:
        match_id = row["match_id"]
        meta = json.loads(body)
        start_time_iso = unix_to_iso(meta["match_info"]["start_time"])
        era_id = era_id_for(self.conn, start_time_iso)
        shop_item_ids = {r["item_id"] for r in
                         self.conn.execute("SELECT item_id FROM items").fetchall()}
        parsed = parse_metadata(meta, body, shop_item_ids, era_id, fetched_at)

        # One transaction: the match rows and the queue flip commit together.
        with self.conn:
            insert_match(self.conn, parsed)
            self.conn.execute(
                "UPDATE fetch_queue SET status = 'fetched', last_attempt_at = ?,"
                " next_retry_at = NULL, last_error = NULL WHERE match_id = ?",
                (fetched_at, match_id),
            )

        self.transient_strikes = 0  # any 200 resets the streak
        log.info("match %s pending -> fetched", match_id)
        return "fetched"

    def _handle_match_fault(self, row, status: int, fetched_at: str) -> str:
        match_id = row["match_id"]
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            self.conn.execute(
                "UPDATE fetch_queue SET status = 'unavailable', attempts = ?,"
                " last_attempt_at = ?, last_error = ? WHERE match_id = ?",
                (attempts, fetched_at, f"HTTP {status}", match_id),
            )
            self.conn.commit()
            log.info("match %s -> unavailable (gave up after %d attempts)", match_id, attempts)
            return "unavailable"

        next_retry = self._backoff_iso(attempts)
        self.conn.execute(
            "UPDATE fetch_queue SET status = 'failed', attempts = ?, last_attempt_at = ?,"
            " next_retry_at = ?, last_error = ? WHERE match_id = ?",
            (attempts, fetched_at, next_retry, f"HTTP {status}", match_id),
        )
        self.conn.commit()
        log.info("match %s -> failed (attempt %d, retry at %s)", match_id, attempts, next_retry)
        return "failed"

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
