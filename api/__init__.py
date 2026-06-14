"""Read-only presentation API for the Deadlock stat tracker.

Deliberately empty: importing `api.service` (the shared query+stats layer used
by both the FastAPI app and the `stats` CLI) must not drag in FastAPI. Only
`api.app` imports the web framework.
"""
