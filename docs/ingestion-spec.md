# Deadlock Stat Tracker: Ingestion Worker Behavior Spec

Companion to the data model doc. This describes how the background process that keeps the database fresh should behave. No language is assumed, though the notes lean Python since that's the likely starting point.

## What the worker is

A single long-running (or regularly launched) process with three jobs, run as separate loops on different schedules:

| Loop | Purpose | Cadence |
|---|---|---|
| Discovery | Find new match IDs for tracked accounts | Every 30 min |
| Drain | Fetch full metadata for queued matches | Continuous, rate-limited |
| Maintenance | Re-queue stale failures, refresh baselines and assets | Nightly |

Keeping them separate matters: discovery is cheap (one match-history call per account), draining is the expensive part, and maintenance is housekeeping. Different costs, different schedules.

## Loop 1: Discovery

```
every 30 minutes:
    for each account in tracked_accounts:
        history = GET match-history(account_id)
        for match in history where match_id > sync_state.last_match_id:
            INSERT OR IGNORE INTO fetch_queue (match_id, status='pending')
        update sync_state.last_match_id to max seen
        update sync_state.last_synced_at
```

Design notes:

- **`INSERT OR IGNORE` makes discovery idempotent.** If you and your brother both appear in the same match, it gets discovered twice but queued once. Idempotency (safe to run the same operation repeatedly) is the property to aim for everywhere in this system.
- **The high-water mark (`last_match_id`) makes restarts cheap.** If the process dies and restarts, it doesn't re-walk your entire history, just everything newer than the mark.
- 30 minutes is deliberate. Deadlock matches run 25 to 45 minutes, so polling faster buys almost nothing and just burns the API's goodwill.

## Loop 2: Drain (the rate-limited heart)

```
forever:
    row = SELECT from fetch_queue
          WHERE status = 'pending'
             OR (status = 'failed' AND now() > next_retry_at)
          ORDER BY discovered_at
          LIMIT 1
    if no row: sleep 60s, continue

    wait_for_token()                  # rate limiter, see below
    response = GET match-metadata(row.match_id)

    case response:
        200 -> parse (see Parsing notes), write matches / match_players /
            match_item_purchases in ONE transaction, mark status='fetched'
        404 or "not yet available" ->
               attempts += 1
               if attempts >= 5: status='unavailable'
               else: status='failed', next_retry_at = backoff(attempts)
        429 -> do NOT increment attempts (this is OUR fault, not the match's)
               sleep for Retry-After header if present, else 5 minutes
        5xx or timeout ->
               attempts += 1, status='failed', next_retry_at = backoff(attempts)
```

### Rate limiting

Use a token bucket, the standard pattern and worth learning by name:

- The bucket holds up to `burst` tokens (say 5) and refills at `rate` tokens per second (say 0.2, i.e. one request every 5 seconds).
- Every API call costs one token. No token available means the worker sleeps until one is.
- Add jitter: after each call, sleep an extra random 0 to 2 seconds. Perfectly periodic traffic is the signature of a bot misbehaving; jitter makes you a polite citizen and avoids thundering-herd patterns if you ever run more than one worker.

Start very conservative (one request per 5 seconds is only ~17k/day, far more than a personal tracker needs) and check deadlock-api's published limits and headers before tuning up. They're community-run on donations; treat their servers the way you wish Valve treated your match report unlocks.

### Exponential backoff

```
backoff(attempts) = min(base * 2^attempts, cap) + jitter
e.g. base = 10 min, cap = 24 h
attempt 1 -> ~10 min, 2 -> ~20 min, 3 -> ~40 min ... capped at a day
```

The intuition: if something failed just now, it'll probably still fail in one second, but it might work in ten minutes. Doubling the wait each time means transient problems resolve quickly while persistent ones stop wasting requests.

### The two failure kinds (this distinction does a lot of work)

- **The match's fault** (404, not yet parsed, metadata missing): increment `attempts`, eventually give up to `unavailable`. These are your throttled old match reports.
- **Our fault or the world's fault** (429, network blips, 5xx): never count against the match. The match is fine; the conditions weren't. Retrying it later costs nothing.

Mixing these up is a classic ingestion bug: a flaky network at 3 a.m. permanently marks 50 perfectly good matches as unavailable.

### Parsing notes

The metadata payload has no flat damage or healing fields. Each player
carries a `stats` time series sampled across the match, and the final
totals are whatever the LAST entry of that series holds. The parser
extracts player_damage, obj_damage, and healing from there (see
docs/api-findings.md for the exact shape).

Two rules follow:

- **A missing or empty series yields NULLs, never zeros.** A zero is a
  claim ("this player did no damage"); a NULL is an admission ("we don't
  know"). Averages and baselines must not be polluted by fake zeros.
- **The full series stays in raw_json untouched.** This finding confirms
  the per-minute data exists, which means future features (soul curves,
  death timing) backfill from already-ingested matches with no
  re-fetching. Do not extract more than the finals for now.

### Crash safety

Two rules make the worker survivable:

1. **All progress lives in the database, none in memory.** The queue, the high-water marks, and retry timestamps are all rows. Kill the process at any moment and restarting resumes exactly where it left off. This is why the data model has `fetch_queue` at all.
2. **One match = one transaction.** Either all of a match's rows land (match, 12 players, item purchases) or none do. A crash mid-write can't leave a half-ingested match that poisons later stats.

## Loop 3: Maintenance (nightly)

```
once per day (e.g. 4 a.m.):
    -- second chances: Valve's unlock throttle means yesterday's
    -- 'unavailable' may be fetchable today
    UPDATE fetch_queue
       SET status='pending', attempts=0
     WHERE status='unavailable'
       AND last_attempt_at < now() - 24h;

    refresh baselines, one request per era:
        for each era in patch_eras, plus one explicit all-time span, call analytics with min/max date params set to that era's
        boundaries. NEVER omit the date params: the endpoint defaults to a trailing 30-day window, which would silently store recent-meta numbers under an older era's label.
        (new snapshot_id; old snapshots kept for time-travel debugging)

    refresh heroes / items from assets API

    log a one-line summary:
        "discovered X, fetched Y, failed Z, unavailable W, queue depth Q"
```

That nightly re-queue line is the entire automation story for your old match reports: every time Valve lets you unlock another batch, the tracker absorbs them within a day with zero manual steps.

## How it actually runs

Three reasonable shapes, in order of complexity:

1. **A script you run when you play** (`python ingest.py`): runs discovery once, drains until the queue is empty, exits. Simplest possible start, and totally fine for week one.
2. **A scheduled task** (Windows Task Scheduler / cron) running that same script hourly. Set-and-forget without long-running process headaches. Probably the right MVP shape.
3. **A persistent daemon** with an internal scheduler (e.g. APScheduler) running all three loops. The "real" shape, and what you'd want once a web UI reads the database live.

Because all state is in SQLite, these are the *same code* invoked differently. You can graduate from 1 to 3 without rearchitecting, which is the payoff of the crash-safety rules above.

## Observability (don't skip this)

A worker that runs silently in the background will fail silently in the background. Minimum viable visibility:

- Log every state transition: `match 38221904 pending -> fetched (1.2s)`.
- Log every rate-limit event and backoff decision.
- A tiny `status` command (or later, a UI badge) that prints queue depth, last discovery time, and counts by status. If `unavailable` climbs or `last_synced_at` goes stale, you want to notice in seconds, not weeks.

## Settled defaults (tune later, with reasons logged)

| Knob | Default | Why |
|---|---|---|
| Discovery interval | 30 min | Matches your match length; faster is waste |
| Drain rate | 1 req / 5 s + jitter | Polite to a donation-funded API |
| Max attempts | 5 | Past 5, it's a throttle problem, not a retry problem |
| Backoff base / cap | 10 min / 24 h | Spans "blip" to "wait for Valve" |
| Re-queue window | 24 h | Matches the unlock-throttle rhythm |

## Test scenarios the implementation must pass

Worth writing down now, because these become your test suite and the acceptance criteria in your Claude Code prompt:

1. Run discovery twice in a row: queue contains no duplicates.
2. Kill the process mid-drain, restart: no match is lost or double-written.
3. Simulate a 429: worker slows down and the match's `attempts` is unchanged.
4. Simulate five 404s on one match: it lands in `unavailable`; nightly job revives it.
5. Two tracked accounts in the same match: one queue row, one fetch, both visible in stats.
