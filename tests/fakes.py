"""Test doubles for the ingestion worker: fake HTTP client, clocks, sleep.

No test touches the network (CLAUDE.md hard rule 3); everything HTTP-shaped
goes through FakeClient with canned (status, headers, body) responses.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """Stands in for ingest.client.Client.

    Routes are (substring, [responses]) pairs; a response is either a
    (status, headers, body) tuple or an Exception instance to raise.
    When a route has multiple responses they are consumed in order and
    the last one repeats.
    """

    def __init__(self):
        self.calls: list[str] = []
        self._routes: list[tuple[str, list]] = []

    def add(self, pattern: str, *responses) -> None:
        self._routes.append((pattern, list(responses)))

    def get(self, url: str):
        self.calls.append(url)
        for pattern, responses in self._routes:
            if pattern in url:
                response = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"FakeClient has no route for {url}")

    def calls_matching(self, pattern: str) -> list[str]:
        return [u for u in self.calls if pattern in u]


def ok(body) -> tuple[int, dict, str]:
    if not isinstance(body, str):
        body = json.dumps(body)
    return (200, {}, body)


class FakeSleep:
    """Records every sleep; optionally advances a ManualNow clock."""

    def __init__(self, now: "ManualNow | None" = None):
        self.calls: list[float] = []
        self._now = now

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._now is not None:
            self._now.advance(seconds)


class ManualNow:
    """Injectable replacement for utcnow(): call to read, advance() to move."""

    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
