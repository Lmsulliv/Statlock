"""Shared spike helper: rate-limited GET + raw response archiving.

Hard rule: never exceed 1 request / 5 s to deadlock-api.com, even in spikes.
The throttle timestamp is persisted to disk (out/.last_deadlock_request) so
the limit holds across separate spike-script runs, not just within one
process.
"""
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

_STAMP = OUT / ".last_deadlock_request"
_MIN_INTERVAL_S = 5.0
_UA = "deadlock-stat-tracker-spike/0.1 (personal project)"


def _wait_for_slot() -> None:
    try:
        last = float(_STAMP.read_text())
    except (FileNotFoundError, ValueError):
        last = 0.0
    wait = _MIN_INTERVAL_S - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _STAMP.write_text(str(time.time()))


def get(url: str, throttle: bool = True) -> tuple[int, str]:
    """GET url, return (status_code, body_text).

    throttle=True enforces the 5-second spacing; only pass False for hosts
    other than deadlock-api.com (e.g. the Steam Web API).
    """
    if throttle:
        _wait_for_slot()
    print(f"[{time.strftime('%H:%M:%S')}] GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def save_raw(name: str, body: str) -> Path:
    path = OUT / name
    path.write_text(body, encoding="utf-8")
    print(f"  saved out/{name} ({len(body)} bytes)")
    return path
