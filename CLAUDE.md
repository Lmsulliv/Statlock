# Deadlock Stat Tracker

A personal Deadlock statistics web app: ingests match data from
deadlock-api.com into SQLite, computes statistically honest performance
analytics (Wilson intervals, Bayesian shrinkage vs. global baselines),
and serves them through a FastAPI backend and React frontend.

## Authoritative documents

The full design lives in three spec docs. Read the relevant one before
working on its layer; when code and spec disagree, flag it instead of
silently picking one:

- docs/data-model.md         (schema, views, statistics formulas)
- docs/ingestion-spec.md     (worker loops, rate limiting, failure handling)
- docs/presentation-spec.md  (screens, API contracts, presentation rules)
- docs/api-findings.md       (verified facts about the live API; trumps
  assumptions in the other docs)

## Stack

- Python 3.12+, FastAPI, SQLite (stdlib sqlite3 or SQLAlchemy Core; no ORM models)
- pytest for all backend tests
- React + Vite frontend in frontend/ (Phase 5 only)
- Runtime dependencies require asking first. Dev-only dependencies
  (testing, linting, type checking) may be proposed freely but still
  need approval before installing.

## Hard rules

1. Statistics math (Wilson, shrinkage, verdicts) lives ONLY in
   stats/ and is imported everywhere else. The frontend never computes
   statistics; it renders what the API returns.
2. All API responses from deadlock-api get archived raw (raw_json)
   before any parsing.
3. Never exceed 1 request per 5 seconds to deadlock-api, including
   during development and tests. Tests must mock HTTP; no test may hit
   the live API.
4. One match = one database transaction.
5. Distinguish failure kinds per the ingestion spec: 429s and network
   errors never increment a match's attempt counter.
6. Don't invent fields. If a deadlock-api response field isn't recorded
   in docs/api-findings.md, verify it and record it there first.

## Working style

- I'm a software engineering student learning alongside this project.
  After each meaningful chunk, add a short "why it's built this way"
  note in the PR-style summary. Flag any concept a student might not
  have seen (e.g. token bucket, idempotency) with a one-line definition.
- Test-first: each phase's acceptance scenarios (listed in the specs)
  become pytest tests before implementation.
- Prefer boring, readable code over clever code. Small functions,
  descriptive names, comments only where the why isn't obvious.
- Stop at phase boundaries. Never start the next phase's work without
  being asked.