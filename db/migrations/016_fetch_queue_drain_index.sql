-- Schema v16: index fetch_queue for the drain loop's row selection.
--
-- DrainWorker._next_row runs up to TWICE per drain step (once for fresh
-- pending/failed work, once for due deferred work) and previously had no usable
-- index, so every step full-scanned the whole queue. As the queue grows to tens
-- of thousands of rows during a bulk import, that scan dominates each step.
--
-- The composite (status, next_retry_at, discovered_at) covers all three of the
-- selection query's shapes: an equality on `status` (pending / failed /
-- deferred), a range on `next_retry_at` (the due-retry predicate), and ordering
-- by discovered_at / next_retry_at. Purely additive -- a CREATE INDEX, no table
-- rebuild.

CREATE INDEX idx_fetch_queue_drain
    ON fetch_queue(status, next_retry_at, discovered_at);
