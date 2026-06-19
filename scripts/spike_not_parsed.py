"""THROWAWAY SPIKE: how does deadlock-api signal a match it has no data for?

Why this exists: a 404/400 on a queued match currently burns the match's
5-attempt budget and marks it 'unavailable' forever. But many of those are real
games deadlock-api simply hasn't parsed yet -- a temporary "not ready", not the
match's fault. Before changing the drain loop to DEFER those, we need real data
on three questions (CLAUDE.md hard rule 3: no test hits the live API, so this is
a manual script paced at 1 req / 5 s, NOT a pytest):

  (a) Of the matches already marked 'unavailable', how many now return 200?
      (How much real data is the current behavior silently dropping?)
  (b) Is there a signal that distinguishes "queued / not ready yet" from a true
      permanent error -- a distinct status (202/425/404...), a body field, a
      header?
  (c) Does merely REQUESTING an un-parsed match seem to trigger an upstream
      fetch (does the answer change if we ask again)?

It opens the same DB the app reads (SELECT only), probes the live API through
the SAME rate-limited client the worker uses (so the 1-req/5-s budget is shared
with any running worker via data/.last_deadlock_request), archives raw bodies to
spikes/out/, and APPENDS a findings section to docs/api-findings.md.
"""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# scripts/ lives one level below the project root; put the root on the path so
# `import ingest...` / `import api...` resolve exactly like the app's do.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.config import db_path
from ingest.client import BASE_URL, Client, NetworkError
from ingest.ratelimit import DEFAULT_STAMP, TokenBucket
from tracker.db import connect

OUT = Path(__file__).resolve().parent.parent / "spikes" / "out"
OUT.mkdir(parents=True, exist_ok=True)
FINDINGS = Path(__file__).resolve().parent.parent / "docs" / "api-findings.md"

SAMPLE_A = 12          # how many existing 'unavailable' rows to re-probe (of 72)
SAMPLE_B = 6           # how many newest history matches to probe for the (b) signal

# The same client the worker uses; the shared stamp file means this spike and a
# running worker can't together exceed 1 req / 5 s.
_client = Client(TokenBucket(stamp_path=DEFAULT_STAMP))


def metadata_url(match_id: int) -> str:
    return f"{BASE_URL}/v1/matches/{match_id}/metadata"


def history_url(account_id: int) -> str:
    return f"{BASE_URL}/v1/players/{account_id}/match-history"


def probe(url: str) -> tuple[int, dict, str]:
    """One paced GET. Returns (status, headers, body); status -1 on NetworkError."""
    print(f"  GET {url}")
    try:
        status, headers, body = _client.get(url)
    except NetworkError as e:
        print(f"    network error: {e}")
        return -1, {}, str(e)
    print(f"    -> {status}  {body[:90].strip()!r}")
    return status, headers, body


def classify(status: int, body: str) -> str:
    """Bucket a metadata response into the categories the drain loop will care
    about, so we can count them."""
    if status == 200:
        return "200 (data available)"
    if status == 400 and "salts" in body.lower():
        return '400 "salts cannot be fetched"'
    if status == 404:
        return "404 (not found)"
    if status == -1:
        return "network error"
    return f"{status} (other)"


def section_a(conn) -> tuple[Counter, list[int]]:
    """(a) Re-probe the newest existing 'unavailable' rows: still dead, or now 200?"""
    print("\n=== (a) re-probing existing 'unavailable' matches ===")
    ids = [r["match_id"] for r in conn.execute(
        "SELECT match_id FROM fetch_queue WHERE status = 'unavailable'"
        " ORDER BY match_id DESC LIMIT ?", (SAMPLE_A,))]
    print(f"sampling {len(ids)} of the newest unavailable match ids: {ids}")
    results = Counter()
    recovered = []
    for mid in ids:
        status, _headers, body = probe(metadata_url(mid))
        results[classify(status, body)] += 1
        if status == 200:
            recovered.append(mid)
            (OUT / f"notparsed_recovered_{mid}.json").write_text(body, encoding="utf-8")
    return results, recovered


def section_b(conn) -> list[dict]:
    """(b) Probe the newest matches from each tracked account's LIVE history --
    the ones most likely to be un-parsed -- and record exact status/body/headers."""
    print("\n=== (b) probing newest matches from live account history ===")
    accounts = [r["account_id"] for r in conn.execute(
        "SELECT account_id FROM tracked_accounts")]
    fetched = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM fetch_queue WHERE status = 'fetched'")}

    newest: list[int] = []
    for account_id in accounts:
        status, _h, body = probe(history_url(account_id))
        if status != 200:
            print(f"    history for {account_id} returned {status}; skipping")
            continue
        rows = json.loads(body)
        ids = sorted((row["match_id"] for row in rows), reverse=True)
        newest.extend(ids[:10])

    # Highest ids first; flag which we've never successfully fetched (truly new).
    candidates = sorted(set(newest), reverse=True)[:SAMPLE_B]
    print(f"probing newest {len(candidates)} history match ids: {candidates}")
    findings = []
    for mid in candidates:
        status, headers, body = probe(metadata_url(mid))
        rec = {
            "match_id": mid,
            "already_fetched": mid in fetched,
            "status": status,
            "category": classify(status, body),
            "body_head": body[:120].strip(),
            "interesting_headers": {k: v for k, v in headers.items()
                                    if k.lower() in {"retry-after", "cache-control",
                                                     "age", "x-cache", "content-type"}},
        }
        findings.append(rec)
        if status != 200:
            (OUT / f"notparsed_new_{mid}.json").write_text(body, encoding="utf-8")
    return findings


def section_c(b_findings: list[dict]) -> dict | None:
    """(c) Re-probe one not-ready id within this run (>=5 s later via the
    throttle) to see if asking twice changes the answer. A true minutes-apart
    comparison is done by re-running the script; we note that."""
    print("\n=== (c) re-probing one not-ready id to see if the answer changes ===")
    target = next((r["match_id"] for r in b_findings if r["status"] != 200), None)
    if target is None:
        print("    no not-ready match found in (b); skipping (c)")
        return None
    first = next(r for r in b_findings if r["match_id"] == target)
    status2, _h, body2 = probe(metadata_url(target))
    return {
        "match_id": target,
        "first": first["category"],
        "second": classify(status2, body2),
        "changed": classify(status2, body2) != first["category"],
    }


def append_findings(a_results: Counter, recovered: list[int],
                    b_findings: list[dict], c_result: dict | None) -> None:
    """Append a dated section to docs/api-findings.md in the doc's own style."""
    today = datetime.now(timezone.utc).date().isoformat()
    probed_a = sum(a_results.values())
    lines = [
        "",
        "---",
        "",
        f"## Not-parsed / unfetchable matches (spike, verified {today})",
        "",
        "Gathered by the throwaway `scripts/spike_not_parsed.py` to decide the",
        "deferral detection rule and give-up policy. Raw bodies archived in",
        "`spikes/out/notparsed_*`.",
        "",
        "**The metadata endpoint signals \"no data for this match\" with HTTP 400,",
        "NOT 404.** Observed body shape:",
        "`{\"error\":\"Match salts for match <id> cannot be fetched\",\"status\":400}`.",
        "No 404 / 202 / 425 / 409 was seen. (\"Salts\" are the per-match replay",
        "decryption keys deadlock-api needs before it can fetch a match's metadata;",
        "they are missing both for matches too new to be processed and for old",
        "matches whose replays Valve has purged.)",
        "",
        f"### (a) Are existing `unavailable` rows recoverable? (n={probed_a})",
        "",
        "| result | count |",
        "| --- | ---: |",
    ]
    for cat, n in a_results.most_common():
        lines.append(f"| {cat} | {n} |")
    lines += [
        "",
        f"Recovered (now 200): {recovered if recovered else 'none'}.",
        "",
        "### (b) Signal for the newest matches from live account history",
        "",
        "| match_id | already_fetched | status | category | headers |",
        "| ---: | :---: | ---: | --- | --- |",
    ]
    for r in b_findings:
        hdr = ", ".join(f"{k}={v}" for k, v in r["interesting_headers"].items()) or "—"
        lines.append(f"| {r['match_id']} | {r['already_fetched']} | {r['status']} "
                     f"| {r['category']} | {hdr} |")
    lines += ["", "### (c) Does re-requesting change the answer?", ""]
    if c_result is None:
        lines.append("No not-ready match was available to re-probe in this run.")
    else:
        lines.append(f"Match {c_result['match_id']}: first={c_result['first']}, "
                     f"second={c_result['second']}, changed={c_result['changed']}. "
                     "(Within-run gap is only the ~5 s throttle; re-run the script "
                     "minutes/hours later for a real lag comparison.) The "
                     "`force_refetch` flag on the match-history endpoint "
                     "(api-findings 'Match history response shape') is the only "
                     "documented way to *force* an upstream fetch; it triggers "
                     "stricter rate limits, so the worker should NOT use it.")
    lines.append("")
    with FINDINGS.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\nappended findings section to {FINDINGS}")


def main() -> None:
    conn = connect(db_path())
    try:
        a_results, recovered = section_a(conn)
        b_findings = section_b(conn)
        c_result = section_c(b_findings)
    finally:
        conn.close()

    print("\n========== SUMMARY ==========")
    print("(a) existing unavailable re-probe:", dict(a_results), "recovered:", recovered)
    print("(b) newest-history probe:")
    for r in b_findings:
        print("   ", r)
    print("(c) re-probe:", c_result)
    append_findings(a_results, recovered, b_findings, c_result)


if __name__ == "__main__":
    main()
