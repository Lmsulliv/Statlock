"""Ingestion worker: discovery, rate-limited drain, nightly maintenance.

See docs/ingestion-spec.md for the behavior contract. Entry point:
python -m ingest run-once | run-daemon | status | add-account.
"""
