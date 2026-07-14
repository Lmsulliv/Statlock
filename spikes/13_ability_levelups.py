"""Spike 13: what ability-investment analysis can we extract from stored data?

Question: the match-metadata `players[].items[]` array mixes shop purchases
with ability level-up entries (same item_id recurring with a changing
upgrade_id; imbued_ability_id present). Ingest drops every entry whose item_id
is not a shop item (parse.py:188-194), so ability-investment data survives ONLY
inside matches.raw_json. Before proposing a feature we need to know:

  (1) can ability level-up entries be cleanly separated from shop purchases and
      ordered by game time for the tracked player?
  (2) are the specific ability and the level reached identifiable via the
      assets tables?
  (3) coverage: do all stored matches carry these entries, including old ones?
  (4) semantics of upgrade_id / imbued_ability_id, and do the distinct ability
      item_ids match the played hero's abilities?

This script is READ-ONLY and makes NO network calls: it reads the local DB
(mode=ro) and the git-tracked assets archive spikes/out/06_assets_items.json.
Findings get transcribed into docs/api-findings.md per hard rule 6.
"""
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Import rawstore the same way the ingest code reads matches.raw_json.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker import rawstore  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "data" / "tracker.db"
ASSETS = Path(__file__).resolve().parent / "out" / "06_assets_items.json"


def load_assets():
    """item_id -> {'type','name','ability_type','heroes'} from the archive."""
    entries = json.loads(ASSETS.read_text(encoding="utf-8"))
    by_id = {}
    for e in entries:
        by_id[e["id"]] = {
            "type": e.get("type"),
            "name": e.get("name"),
            "ability_type": e.get("ability_type"),
            "heroes": e.get("heroes") or [],
        }
    return by_id


def self_account(conn):
    row = conn.execute(
        "SELECT account_id FROM tracked_accounts WHERE is_self = 1"
    ).fetchone()
    return row[0] if row else None


def self_player(meta, account_id):
    """Return the tracked player's dict from a decoded match, or None."""
    for p in meta.get("match_info", {}).get("players", []):
        if p.get("account_id") == account_id:
            return p
    return None


def classify(items, assets):
    """Split a player's items[] into (ability_entries, shop_entries, unknown).

    Ordered by game_time_s. 'unknown' = item_id absent from the assets archive.
    """
    ability, shop, unknown = [], [], []
    for e in sorted(items, key=lambda e: e.get("game_time_s", 0)):
        iid = e.get("item_id")
        meta = assets.get(iid)
        if meta is None:
            unknown.append(e)
        elif meta["type"] == "ability":
            ability.append(e)
        else:  # upgrade (shop) or weapon
            shop.append(e)
    return ability, shop, unknown


def fmt_time(s):
    if s is None:
        return "   ?  "
    return f"{int(s)//60:2d}:{int(s) % 60:02d}"


def main():
    assets = load_assets()
    types = Counter(v["type"] for v in assets.values())
    print(f"assets: {len(assets)} items  types={dict(types)}\n")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    me = self_account(conn)
    print(f"tracked (self) account_id = {me}\n")

    rows = conn.execute(
        "SELECT match_id, start_time, raw_json FROM matches ORDER BY start_time"
    ).fetchall()

    # ── (3) Coverage scan across ALL stored matches ─────────────────────────
    total = 0
    decoded = 0
    self_found = 0
    with_ability = 0
    ability_counts = []
    zero_ability = []       # matches where self player had 0 ability entries
    unknown_ids = Counter()  # item_ids seen but absent from assets archive
    per_match = []          # (start_time, match_id, meta, self player) for detail passes

    for r in rows:
        total += 1
        body = rawstore.load(r["raw_json"])
        if not body:
            continue
        try:
            meta = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        decoded += 1
        p = self_player(meta, me)
        if p is None:
            continue
        self_found += 1
        ability, shop, unknown = classify(p.get("items", []), assets)
        for e in unknown:
            unknown_ids[e.get("item_id")] += 1
        ability_counts.append(len(ability))
        if ability:
            with_ability += 1
        else:
            zero_ability.append((r["start_time"], r["match_id"]))
        per_match.append((r["start_time"], r["match_id"], meta, p))

    print("── Coverage (question 3) ─────────────────────────────────────────")
    print(f"stored matches:                 {total}")
    print(f"decoded from raw_json:          {decoded}")
    print(f"self player present:            {self_found}")
    print(f"self player w/ >=1 ability:     {with_ability}")
    if ability_counts:
        print(f"ability entries per match:      "
              f"min={min(ability_counts)} "
              f"median={statistics.median(ability_counts):.0f} "
              f"max={max(ability_counts)}")
    if per_match:
        print(f"oldest match:                   {per_match[0][0]}")
        print(f"newest match:                   {per_match[-1][0]}")
    print(f"matches with 0 ability entries: {len(zero_ability)}")
    for st, mid in zero_ability[:10]:
        print(f"    {st}  match {mid}")
    if unknown_ids:
        print(f"item_ids not in assets archive: {len(unknown_ids)} distinct, "
              f"{sum(unknown_ids.values())} occurrences")
        for iid, n in unknown_ids.most_common(10):
            print(f"    item_id {iid}  x{n}")
    print()

    # Pick ~5 matches spanning oldest -> newest for detail passes.
    if not per_match:
        print("No matches with the self player found; nothing more to inspect.")
        return
    idx = sorted(set([0,
                      len(per_match)//4,
                      len(per_match)//2,
                      (3*len(per_match))//4,
                      len(per_match)-1]))
    samples = [per_match[i] for i in idx]

    # ── (1) Separation + ordering, one sample match printed in full ─────────
    print("── Ability timeline, ordered by game_time_s (question 1) ─────────")
    st, mid, meta, p = samples[len(samples)//2]  # a mid-history match
    hero_id = p.get("hero_id")
    ability, shop, unknown = classify(p.get("items", []), assets)
    print(f"match {mid}  {st}  hero_id={hero_id}  "
          f"({len(ability)} ability / {len(shop)} shop / {len(unknown)} unknown)")
    print(f"  {'time':>6} {'item_id':>11} {'upg_id':>11} {'imbued':>11} "
          f"{'flags':>6} {'sold':>6}  ability_type  name")
    for e in ability:
        m = assets.get(e.get("item_id"), {})
        print(f"  {fmt_time(e.get('game_time_s')):>6} "
              f"{e.get('item_id'):>11} "
              f"{str(e.get('upgrade_id')):>11} "
              f"{str(e.get('imbued_ability_id')):>11} "
              f"{str(e.get('flags')):>6} "
              f"{fmt_time(e.get('sold_time_s')) if e.get('sold_time_s') else '   -  ':>6}  "
              f"{str(m.get('ability_type')):>12}  {m.get('name')}")
    print()

    # ── (2) & (4) Ability + level identification, across the 5 samples ──────
    print("── Ability/level identification (questions 2 & 4) ────────────────")
    for st, mid, meta, p in samples:
        hero_id = p.get("hero_id")
        ability, shop, unknown = classify(p.get("items", []), assets)
        distinct = {}
        for e in ability:
            distinct.setdefault(e.get("item_id"), []).append(e)
        # Do the distinct ability item_ids belong to this hero?
        belongs = []
        for iid in distinct:
            heroes = assets.get(iid, {}).get("heroes", [])
            belongs.append(hero_id in heroes)
        print(f"match {mid} {st[:10]} hero={hero_id}: "
              f"{len(ability)} entries, {len(distinct)} distinct abilities, "
              f"all-belong-to-hero={all(belongs) if belongs else 'n/a'}")
        for iid, occ in distinct.items():
            m = assets.get(iid, {})
            upgrades = [e.get("upgrade_id") for e in occ]
            times = [fmt_time(e.get("game_time_s")) for e in occ]
            print(f"    {iid}  x{len(occ)}  {m.get('name'):32} "
                  f"upgrade_ids={upgrades}  times={times}")

    # imbued_ability_id: where does it appear -- ability entries or shop entries?
    print()
    print("── imbued_ability_id occurrence (question 4) ─────────────────────")
    imbued_on_ability = 0
    imbued_on_shop = 0
    imbued_examples = []
    for st, mid, meta, p in per_match:
        ability, shop, unknown = classify(p.get("items", []), assets)
        for e in ability:
            if e.get("imbued_ability_id"):
                imbued_on_ability += 1
        for e in shop:
            iai = e.get("imbued_ability_id")
            if iai:
                imbued_on_shop += 1
                if len(imbued_examples) < 8:
                    tgt = assets.get(iai, {})
                    src = assets.get(e.get("item_id"), {})
                    imbued_examples.append(
                        (src.get("name"), iai, tgt.get("name"), tgt.get("type")))
    print(f"non-zero imbued_ability_id on ability entries: {imbued_on_ability}")
    print(f"non-zero imbued_ability_id on shop entries:    {imbued_on_shop}")
    for src, iai, tgt_name, tgt_type in imbued_examples:
        print(f"    shop item {src!r} imbues -> {iai} ({tgt_name!r}, type={tgt_type})")


if __name__ == "__main__":
    main()
