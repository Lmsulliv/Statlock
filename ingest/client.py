"""HTTP layer: every request goes through the token bucket.

Errors split into two shapes the drain loop cares about:
- an HTTP response with a status code (returned, never raised), and
- NetworkError (DNS failure, refused connection, timeout) where no
  response exists at all. The two are handled differently per the spec's
  failure-kind rules, so the type distinction matters.
"""
import logging
import socket
import urllib.error
import urllib.request

from ingest.ratelimit import TokenBucket

log = logging.getLogger(__name__)

BASE_URL = "https://api.deadlock-api.com"
USER_AGENT = "deadlock-stat-tracker/0.2 (personal project)"


class NetworkError(Exception):
    """The request never produced an HTTP response."""


class Client:
    def __init__(self, bucket: TokenBucket, *, user_agent: str = USER_AGENT, timeout: float = 30):
        self._bucket = bucket
        self._user_agent = user_agent
        self._timeout = timeout

    def get(self, url: str) -> tuple[int, dict, str]:
        """GET url, rate-limited. Returns (status, headers, body)."""
        self._bucket.acquire()
        log.debug("GET %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise NetworkError(str(e)) from e


def archive_response(conn, url: str, status: int, body: str, fetched_at: str) -> None:
    """Hard rule 2: archive the raw response before any parsing, own commit."""
    conn.execute(
        "INSERT INTO raw_api_responses(url, status_code, body, fetched_at) VALUES (?, ?, ?, ?)",
        (url, status, body, fetched_at),
    )
    conn.commit()
