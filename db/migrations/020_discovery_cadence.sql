-- Schema v20: activity-aware discovery cadence.
--
-- Why: discovery (ingest/discovery.py) costs one match-history API call per
-- tracked account every 30 minutes, forever. Accounts accumulate permanently
-- (every Steam login tracks one), so the cost grows without bound: at 1 request
-- per 5 s (hard rule 3), ~300 accounts x 1 call / 30 min already saturates the
-- entire request budget with discovery alone -- leaving nothing for the drain
-- loop that fetches the matches those calls discover. The fix is to visit idle
-- accounts less often (an account nobody has played on in weeks does not need a
-- check every 30 min), which needs a per-account "next due" timestamp.
--
-- sync_state.next_discovery_at: when this account is next due for a discovery
-- pass. NULL means "due now" -- so every account that predates this migration is
-- visited once on the next daemon pass and then scheduled by activity. The
-- daemon's discovery pass (ingest.discovery.discover_due) selects only accounts
-- whose next_discovery_at has passed; the manual run-once / discover_all paths
-- still visit everyone (they now stamp a schedule as a side effect).
--
-- discovery_requests: a mailbox from the WEB process to the daemon. The web
-- process must NEVER call the external API itself (that's the daemon's job and
-- the only place the rate limit is enforced), but an idle account's owner
-- hitting their Overview should still trigger a fresh check. So the web process
-- inserts a request row (INSERT OR IGNORE, keyed by account_id) and the daemon
-- treats a requested account as immediately due, discovering it on its next
-- iteration and deleting the row. requested_at is diagnostic only.
--
-- Purely additive: one ADD COLUMN and one CREATE TABLE, no table rebuild.

ALTER TABLE sync_state ADD COLUMN next_discovery_at TEXT;  -- NULL = due now

CREATE TABLE discovery_requests (
    account_id    INTEGER PRIMARY KEY REFERENCES tracked_accounts(account_id),
    requested_at  TEXT NOT NULL
);
