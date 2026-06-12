"""Spike 06: verify /v1/assets/heroes and /v1/assets/items field shapes.

Neither endpoint is recorded in docs/api-findings.md yet (hard rule 6),
and the reference-table loaders need their exact field names. Two GET
requests total, archived raw, plus a printed field summary to transcribe
into the findings doc.
"""
import json
from collections import Counter

from _api import get, save_raw

BASE = "https://api.deadlock-api.com"


def summarize(label: str, rows: list[dict]) -> None:
    print(f"\n=== {label}: {len(rows)} entries ===")
    key_counts = Counter(k for row in rows for k in row)
    for key, n in sorted(key_counts.items()):
        sample = next((row[key] for row in rows if row.get(key) is not None), None)
        preview = json.dumps(sample)
        if len(preview) > 90:
            preview = preview[:90] + "..."
        print(f"  {key:30s} in {n:4d}/{len(rows)}  e.g. {preview}")


def main() -> None:
    status, body = get(f"{BASE}/v1/assets/heroes")
    print(f"  status {status}")
    save_raw("06_assets_heroes.json", body)
    heroes = json.loads(body)
    summarize("heroes", heroes)

    status, body = get(f"{BASE}/v1/assets/items")
    print(f"  status {status}")
    save_raw("06_assets_items.json", body)
    items = json.loads(body)
    summarize("items", items)

    # How do shop items differ from abilities? Look at discriminator fields.
    for field in ("type", "item_slot_type", "shopable", "item_tier", "disabled"):
        values = Counter(json.dumps(row.get(field)) for row in items)
        print(f"\nitems[].{field} value counts: {dict(values.most_common(8))}")


if __name__ == "__main__":
    main()
