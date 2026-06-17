"""Spike 10: verify the /v1/assets/ranks response shape, especially subrank art.

The rank reference loader (tracker/reference.py:load_ranks) stores only tier,
name, color and DERIVES one badge URL per tier (rank{tier}/badge_lg.png),
dropping the API's `images` object. We now want per-SUBRANK badge art (66
notches, "Oracle 4", star icon on subrank 6). Hard rule 6: don't invent fields
-- verify what /v1/assets/ranks actually returns and record it in
docs/api-findings.md before deriving any subrank URL.

This dumps the full per-rank field set and, for the `images` object, every key
plus a sample value, so we can see whether subrank URLs are explicit fields
(e.g. images.large_subrank1..6) or a tier-only function we must extend.

One throttled GET. Run: python spikes/10_assets_ranks.py
"""
import json
from collections import Counter

from _api import OUT, get, save_raw

BASE = "https://api.deadlock-api.com"


def main() -> None:
    status, body = get(f"{BASE}/v1/assets/ranks")
    print(f"  status {status}")
    save_raw("10_assets_ranks.json", body)
    if status != 200:
        print(f"  non-200: {body[:300]}")
        return

    ranks = json.loads(body)
    print(f"\n=== ranks: {len(ranks)} entries ===")

    # Top-level field coverage across all rank objects.
    top_keys = Counter(k for r in ranks for k in r)
    for key, n in sorted(top_keys.items()):
        sample = next((r[key] for r in ranks if r.get(key) is not None), None)
        preview = json.dumps(sample)
        if len(preview) > 100:
            preview = preview[:100] + "..."
        print(f"  {key:18s} in {n:3d}/{len(ranks)}  e.g. {preview}")

    # The images object is the interesting one for subrank art: enumerate every
    # key seen and one sample URL each, so we know exactly what to store/derive.
    img_keys: Counter = Counter()
    img_sample: dict[str, str] = {}
    for r in ranks:
        images = r.get("images") or {}
        if isinstance(images, dict):
            for k, v in images.items():
                img_keys[k] += 1
                img_sample.setdefault(k, v)
    if img_keys:
        print(f"\n=== images.* keys ({len(img_keys)} distinct) ===")
        for k, n in sorted(img_keys.items()):
            print(f"  images.{k:24s} in {n:3d}  e.g. {img_sample[k]}")
    else:
        print("\n  no `images` object on rank entries -- subrank art not API-provided.")

    # Show one full rank object so any nested subrank structure is visible.
    mid = next((r for r in ranks if r.get("tier") in (8, 6)), ranks[-1])
    print("\n=== one full rank object (tier 6/8) ===")
    print(json.dumps(mid, indent=2)[:1500])


if __name__ == "__main__":
    main()
