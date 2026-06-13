"""Spike: does /v1/analytics/item-stats support bucket=hero?

api-findings records that item-stats has NO avg_badge bucket (a literal
bucket=avg_badge returns 400), but its bucket enum does list a `hero`
value. If bucket=hero returns per-hero rows in one call, the nightly job
can fill baseline_hero_item_stats cheaply instead of paying one request per
hero per era. Hard rule 6: verify before relying on it.

Two throttled calls, both archived: a plain call (baseline) and the
bucket=hero call. Run: python spikes/07_item_stats_hero_bucket.py
"""
import json

from _api import OUT, get, save_raw

BASE = "https://api.deadlock-api.com/v1/analytics/item-stats"


def summarize(label: str, status: int, body: str) -> None:
    print(f"\n{label}: HTTP {status}")
    if status != 200:
        print(f"  body: {body[:200]}")
        return
    data = json.loads(body)
    print(f"  rows: {len(data)}")
    if data:
        keys = sorted(data[0].keys())
        print(f"  row keys: {keys}")
        print(f"  has hero_id: {'hero_id' in keys}")
        if "hero_id" in keys:
            heroes = {row.get("hero_id") for row in data}
            print(f"  distinct hero_id values: {len(heroes)} (sample {sorted(heroes)[:8]})")


def main() -> None:
    status_plain, body_plain = get(f"{BASE}?bucket=no_bucket")
    save_raw("07_item_stats_no_bucket.json", body_plain)
    summarize("bucket=no_bucket", status_plain, body_plain)

    status_hero, body_hero = get(f"{BASE}?bucket=hero")
    save_raw("07_item_stats_bucket_hero.json", body_hero)
    summarize("bucket=hero", status_hero, body_hero)

    verdict = (
        "USABLE: per-hero rows in one call" if status_hero == 200
        and "hero_id" in (json.loads(body_hero)[0] if body_hero.strip().startswith("[") and json.loads(body_hero) else {})
        else "NOT usable: see status/body above"
    )
    print(f"\nVERDICT: bucket=hero -> {verdict}")
    print(f"(raw responses archived under {OUT})")


if __name__ == "__main__":
    main()
