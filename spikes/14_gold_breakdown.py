"""Spike 14: what soul-income (gold) breakdown can we extract from stored data?

Question: docs/api-findings.md records that each match_info.players[].stats[]
snapshot carries a cumulative gold breakdown (gold_lane_creep, gold_player,
gold_neutral_creep, "…") at a 180s-then-300s cadence, but never enumerates the
full field set or checks it against net_worth. Before designing a later
"souls over time vs your rank" feature we need to know, from data we ALREADY
store in matches.raw_json (no new fetch):

  (1) the complete set of gold_* fields (and the gold_sources[] array) and what
      each appears to represent, incl. boss / denies / treasure(urn) income;
  (2) whether the breakdown sums (or nearly sums) to net_worth, i.e. is it a
      complete decomposition of income or only a partial one;
  (3) population reliability across match age -- the later baseline pools all
      12 players of every stored match, so we need per-age coverage of the
      fields, incl. anonymized account_id == 0 players and gold_sources[];
  (4) [writeup only] the feature design -- see spikes/14_gold_breakdown.md.

This script is READ-ONLY and makes NO network calls: it reads the local DB
(mode=ro) and, for a cross-check, the two git-tracked fixtures under
spikes/out/. Findings get transcribed into docs/api-findings.md per hard rule 6.
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
OUT = Path(__file__).resolve().parent / "out"
FIXTURES = [
    OUT / "02_match_metadata_86714494.json",
    OUT / "05_match_metadata_86707774.json",
]

# gold_death_loss is a *debit* (souls lost on death), not an income source; the
# rest are income credits. Kept in a set so the sum check can subtract it.
DEBIT_FIELDS = {"gold_death_loss"}


def players_of(meta):
    return meta.get("match_info", {}).get("players", []) or []


def snapshots_of(player):
    return player.get("stats", []) or []


def gold_scalar_keys(snapshot):
    """Every top-level scalar key of a snapshot whose name mentions gold.

    Excludes gold_sources (an array, handled separately)."""
    return [
        k for k in snapshot
        if "gold" in k.lower() and k != "gold_sources"
    ]


def as_num(v):
    """Treat None/absent as 0 for summation but let callers detect nulls first."""
    return v if isinstance(v, (int, float)) else 0


def iter_matches(conn):
    """Yield (match_id, start_time, era_id, meta) for every decodable match,
    reading one raw_json body at a time (ingest/reprocess.py pattern)."""
    ids = conn.execute(
        "SELECT match_id, start_time, era_id FROM matches ORDER BY start_time"
    ).fetchall()
    for row in ids:
        body = rawstore.load(
            conn.execute(
                "SELECT raw_json FROM matches WHERE match_id = ?",
                (row["match_id"],),
            ).fetchone()["raw_json"]
        )
        if not body:
            continue
        try:
            meta = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        yield row["match_id"], row["start_time"], row["era_id"], meta


# ─────────────────────────────────────────────────────────────────────────────
# Question 1 — enumerate the fields
# ─────────────────────────────────────────────────────────────────────────────
def question1_field_enumeration(all_matches, samples):
    print("== Q1: gold field enumeration "
          "==============================================")

    field_present = Counter()      # scalar gold key -> snapshots where present
    field_null = Counter()         # ... where present but value is None
    field_nonzero_final = Counter()  # ... nonzero at a player's LAST snapshot
    final_share = defaultdict(list)  # key -> [value / net_worth] at last snap
    monotone_pairs = Counter()     # key -> adjacent snapshot pairs compared
    monotone_ok = Counter()        # ... that were non-decreasing
    total_snaps = 0
    final_snaps = 0

    source_present = Counter()     # gold_sources 'source' id -> snapshots seen
    source_has_kills = Counter()   # ... with a non-null kills field
    source_has_damage = Counter()  # ... with a non-null damage field
    source_gold_final = defaultdict(list)  # id -> [gold+gold_orbs] at last snap

    for match_id, start_time, era_id, meta in all_matches:
        for p in players_of(meta):
            snaps = snapshots_of(p)
            if not snaps:
                continue
            snaps = sorted(snaps, key=lambda s: s.get("time_stamp_s", 0))
            for s in snaps:
                total_snaps += 1
                for k in gold_scalar_keys(s):
                    field_present[k] += 1
                    if s[k] is None:
                        field_null[k] += 1
                for src in s.get("gold_sources", []) or []:
                    sid = src.get("source")
                    source_present[sid] += 1
                    if src.get("kills") is not None:
                        source_has_kills[sid] += 1
                    if src.get("damage") is not None:
                        source_has_damage[sid] += 1
            # Monotonicity: income credits should be non-decreasing over time.
            for prev, cur in zip(snaps, snaps[1:]):
                for k in gold_scalar_keys(cur):
                    if k in prev and prev[k] is not None and cur[k] is not None:
                        monotone_pairs[k] += 1
                        if cur[k] >= prev[k]:
                            monotone_ok[k] += 1
            # Final-snapshot shares.
            last = snaps[-1]
            nw = as_num(last.get("net_worth"))
            final_snaps += 1
            for k in gold_scalar_keys(last):
                v = last[k]
                if v is None:
                    continue
                if v != 0:
                    field_nonzero_final[k] += 1
                if nw:
                    final_share[k].append(v / nw)
            for src in last.get("gold_sources", []) or []:
                sid = src.get("source")
                source_gold_final[sid].append(
                    as_num(src.get("gold")) + as_num(src.get("gold_orbs")))

    print(f"\nscanned {final_snaps} player-timelines, {total_snaps} snapshots\n")
    print("scalar gold_* fields (share = mean value / net_worth at last snap):")
    print(f"  {'field':<26}{'present%':>9}{'null%':>7}"
          f"{'nonzero@end%':>13}{'monotone%':>11}{'mean_share':>11}")
    for k in sorted(field_present, key=lambda k: -sum(final_share.get(k, [0]))):
        pres = 100 * field_present[k] / total_snaps
        nul = 100 * field_null[k] / max(1, field_present[k])
        nz = 100 * field_nonzero_final[k] / max(1, final_snaps)
        mon = (100 * monotone_ok[k] / monotone_pairs[k]
               if monotone_pairs[k] else float("nan"))
        share = statistics.mean(final_share[k]) if final_share.get(k) else 0.0
        sign = "-" if k in DEBIT_FIELDS else " "
        print(f" {sign}{k:<26}{pres:>8.1f}%{nul:>6.1f}%{nz:>12.1f}%"
              f"{mon:>10.1f}%{share:>+11.2%}")

    print("\ngold_sources[] entries (source id -> observed nature):")
    print(f"  {'source':>7}{'present%':>10}{'has_kills%':>12}"
          f"{'has_damage%':>13}{'mean_gold@end':>15}")
    tot_src_snaps = max(1, total_snaps)
    for sid in sorted(source_present):
        pres = 100 * source_present[sid] / tot_src_snaps
        hk = 100 * source_has_kills[sid] / max(1, source_present[sid])
        hd = 100 * source_has_damage[sid] / max(1, source_present[sid])
        mg = (statistics.mean(source_gold_final[sid])
              if source_gold_final.get(sid) else 0.0)
        print(f"  {sid:>7}{pres:>9.1f}%{hk:>11.1f}%{hd:>12.1f}%{mg:>15.0f}")

    # Print one full snapshot key list from the oldest and newest sampled match.
    print("\nfull snapshot key list (oldest vs newest sampled match):")
    for label, (match_id, start_time, era_id, meta) in (
        ("oldest", samples[0]), ("newest", samples[-1]),
    ):
        snap = None
        for p in players_of(meta):
            if snapshots_of(p):
                snap = snapshots_of(p)[0]
                break
        keys = sorted(snap.keys()) if snap else []
        golds = [k for k in keys if "gold" in k.lower()]
        print(f"  {label} match {match_id} ({start_time[:10]}): "
              f"{len(keys)} keys, gold-related = {golds}")


def question1_fixture_crosscheck():
    print("\n-- Q1 cross-check against git-tracked fixtures ------------------")
    for path in FIXTURES:
        meta = json.loads(path.read_text(encoding="utf-8"))
        golds, sources = set(), set()
        for p in players_of(meta):
            for s in snapshots_of(p):
                golds.update(gold_scalar_keys(s))
                for src in s.get("gold_sources", []) or []:
                    sources.add(src.get("source"))
        print(f"  {path.name}: {len(golds)} scalar gold fields, "
              f"source ids {sorted(sources)}")
        print(f"    fields = {sorted(golds)}")


# ─────────────────────────────────────────────────────────────────────────────
# Question 2 — does the breakdown sum to net_worth?
# ─────────────────────────────────────────────────────────────────────────────
def reconstructions(snapshot):
    """Candidate reconstructions of net_worth from the breakdown fields.

    Returns {label: reconstructed_value}. The winning candidate tells us whether
    the archived breakdown is a COMPLETE decomposition of net worth or partial.
    """
    scalar_credit = sum(
        as_num(snapshot.get(k))
        for k in gold_scalar_keys(snapshot) if k not in DEBIT_FIELDS
    )
    death_loss = as_num(snapshot.get("gold_death_loss"))
    src_gold = sum(as_num(s.get("gold"))
                   for s in snapshot.get("gold_sources", []) or [])
    src_orbs = sum(as_num(s.get("gold_orbs"))
                   for s in snapshot.get("gold_sources", []) or [])
    return {
        "scalar_credits": scalar_credit,
        "scalar_credits - death_loss": scalar_credit - death_loss,
        "sources(gold)": src_gold,
        "sources(gold+orbs)": src_gold + src_orbs,
        "sources(gold+orbs) - death_loss": src_gold + src_orbs - death_loss,
    }


def question2_sum_check(all_matches):
    print("\n== Q2: does the breakdown sum to net_worth "
          "====================================")
    # residuals[label] = list of (net_worth - reconstruction) at final snapshots
    final_resid = defaultdict(list)
    mid_resid = defaultdict(list)   # nearest snapshot with time_stamp_s <= 600
    final_nw = []
    best_final = []                 # residual for the sources(gold+orbs) candidate
    tail = {"reconciled": [], "divergent": []}  # (net_worth, n_snaps, last_ts)

    for match_id, start_time, era_id, meta in all_matches:
        for p in players_of(meta):
            snaps = sorted(snapshots_of(p),
                           key=lambda s: s.get("time_stamp_s", 0))
            if not snaps:
                continue
            last = snaps[-1]
            nw = as_num(last.get("net_worth"))
            final_nw.append(nw)
            recon = reconstructions(last)
            resid = nw - recon["sources(gold+orbs)"]
            best_final.append(resid)
            if nw:
                bucket = ("divergent" if abs(resid) / nw > 0.25
                          else "reconciled" if abs(resid) / nw <= 0.05 else None)
                if bucket:
                    tail[bucket].append((nw, len(snaps), last.get("time_stamp_s")))
            for label, val in recon.items():
                final_resid[label].append(nw - val)
            mid = None
            for s in snaps:
                if s.get("time_stamp_s", 0) <= 600:
                    mid = s
            if mid is not None:
                nwm = as_num(mid.get("net_worth"))
                for label, val in reconstructions(mid).items():
                    mid_resid[label].append(nwm - val)

    def summarize(resid, nw_ref):
        base = statistics.mean(nw_ref) if nw_ref else 1
        print(f"  {'reconstruction':<34}{'mean_resid':>12}"
              f"{'median':>10}{'p95|resid|':>12}{'mean|resid|/nw':>16}")
        for label, xs in resid.items():
            if not xs:
                continue
            absxs = sorted(abs(x) for x in xs)
            p95 = absxs[int(0.95 * (len(absxs) - 1))]
            print(f"  {label:<34}{statistics.mean(xs):>12.0f}"
                  f"{statistics.median(xs):>10.0f}{p95:>12.0f}"
                  f"{statistics.mean(absxs) / base:>15.2%}")

    def completeness(resid_pairs, label):
        """How close is the best candidate to net_worth, per-timeline?

        resid_pairs = [(net_worth, residual)]. Buckets |residual|/net_worth so
        we can say "complete for X% of timelines" rather than lean on a mean the
        tail dominates."""
        fr = [abs(r) / nw for nw, r in resid_pairs if nw]
        fr.sort()
        n = len(fr)
        def pct(thresh):
            return 100 * sum(1 for x in fr if x <= thresh) / n
        print(f"  {label}: |resid|/net_worth <=1% for {pct(0.01):.0f}%, "
              f"<=2% for {pct(0.02):.0f}%, <=5% for {pct(0.05):.0f}%, "
              f">25% for {100 - pct(0.25):.0f}% of {n} timelines")

    # Reconciliation split by game mode: the "complete vs partial" answer hinges
    # on it. match_info.game_mode is 1 for normal/ranked, 4 for the bot-like mode
    # whose net_worth is a synthetic ramp with an all-zero gold breakdown.
    by_mode = defaultdict(lambda: [0, 0])   # game_mode -> [reconciled, total]
    for match_id, start_time, era_id, meta in all_matches:
        gm = meta.get("match_info", {}).get("game_mode")
        for p in players_of(meta):
            snaps = sorted(snapshots_of(p),
                           key=lambda s: s.get("time_stamp_s", 0))
            if not snaps:
                continue
            last = snaps[-1]
            nw = as_num(last.get("net_worth"))
            if not nw:
                continue
            recon = reconstructions(last)["sources(gold+orbs)"]
            by_mode[gm][0] += 1 if abs(nw - recon) / nw <= 0.05 else 0
            by_mode[gm][1] += 1

    print("\nreconcile-within-5% (sources gold+orbs) by match_info.game_mode:")
    for gm, (ok, tot) in sorted(by_mode.items(), key=lambda kv: -kv[1][1]):
        print(f"  game_mode={gm}: {ok}/{tot} = {100 * ok / tot:.0f}%")

    print(f"\nfinal snapshot ({len(final_nw)} player-timelines, "
          f"mean net_worth {statistics.mean(final_nw):.0f}):")
    summarize(final_resid, final_nw)
    completeness(list(zip(final_nw, best_final)), "final, sources(gold+orbs)")
    for name, rows in tail.items():
        if rows:
            nws, ns, ts = zip(*[(a, b, c or 0) for a, b, c in rows])
            print(f"    {name:<11} n={len(rows):>4}  mean_net_worth={statistics.mean(nws):>7.0f}"
                  f"  mean_snaps={statistics.mean(ns):>4.1f}"
                  f"  mean_last_ts={statistics.mean(ts):>6.0f}s")
    print("\nmid-lane snapshot (latest with time_stamp_s <= 600):")
    summarize(mid_resid, final_nw)


# ─────────────────────────────────────────────────────────────────────────────
# Question 3 — population reliability across match age
# ─────────────────────────────────────────────────────────────────────────────
def question3_coverage(all_matches):
    print("\n== Q3: population reliability across match age "
          "=================================")
    # Per calendar month: match count, players-with-stats, players with full
    # scalar gold set present, players with gold_sources[], anon (acct 0) count.
    by_month = defaultdict(lambda: {
        "matches": 0, "players": 0, "with_stats": 0,
        "full_gold": 0, "with_sources": 0, "anon": 0, "anon_with_stats": 0,
    })
    by_era = defaultdict(lambda: {"matches": 0, "with_sources_players": 0,
                                  "players": 0})
    # Reference scalar field set = union seen anywhere (filled on first pass is
    # awkward with a generator, so recompute a canonical set from the fixtures).
    ref_fields = set()
    for path in FIXTURES:
        meta = json.loads(path.read_text(encoding="utf-8"))
        for p in players_of(meta):
            for s in snapshots_of(p):
                ref_fields.update(gold_scalar_keys(s))

    for match_id, start_time, era_id, meta in all_matches:
        month = start_time[:7]
        m = by_month[month]
        e = by_era[era_id]
        m["matches"] += 1
        e["matches"] += 1
        for p in players_of(meta):
            m["players"] += 1
            e["players"] += 1
            is_anon = p.get("account_id", 0) == 0
            if is_anon:
                m["anon"] += 1
            snaps = snapshots_of(p)
            if not snaps:
                continue
            m["with_stats"] += 1
            if is_anon:
                m["anon_with_stats"] += 1
            last = snaps[-1]
            present = {k for k in gold_scalar_keys(last) if last[k] is not None}
            if ref_fields <= present:
                m["full_gold"] += 1
            if last.get("gold_sources"):
                m["with_sources"] += 1
                e["with_sources_players"] += 1

    print(f"\nreference scalar gold field set ({len(ref_fields)}): "
          f"{sorted(ref_fields)}\n")
    print(f"  {'month':<9}{'matches':>8}{'players':>8}{'stats%':>8}"
          f"{'fullgold%':>11}{'sources%':>10}{'anon':>6}{'anon_stats%':>12}")
    for month in sorted(by_month):
        d = by_month[month]
        pl = max(1, d["players"])
        anon = max(1, d["anon"])
        print(f"  {month:<9}{d['matches']:>8}{d['players']:>8}"
              f"{100*d['with_stats']/pl:>7.0f}%{100*d['full_gold']/pl:>10.0f}%"
              f"{100*d['with_sources']/pl:>9.0f}%{d['anon']:>6}"
              f"{100*d['anon_with_stats']/anon:>11.0f}%")

    print(f"\n  {'era':>5}{'matches':>8}{'players':>8}{'sources%':>10}")
    for era_id in sorted(by_era, key=lambda x: (x is None, x)):
        d = by_era[era_id]
        pl = max(1, d["players"])
        print(f"  {str(era_id):>5}{d['matches']:>8}{d['players']:>8}"
              f"{100*d['with_sources_players']/pl:>9.0f}%")


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"stored matches: {total}\n")

    # Materialize the decoded matches once (480 x ~1.5MB decode is the slow part);
    # keeping only what the three questions need would be leaner, but for a spike
    # holding the decoded dicts is simplest and fits in memory.
    all_matches = list(iter_matches(conn))
    print(f"decoded from raw_json: {len(all_matches)}")
    idx = sorted({0, len(all_matches) // 4, len(all_matches) // 2,
                  (3 * len(all_matches)) // 4, len(all_matches) - 1})
    samples = [all_matches[i] for i in idx]

    question1_field_enumeration(all_matches, samples)
    question1_fixture_crosscheck()
    question2_sum_check(all_matches)
    question3_coverage(all_matches)


if __name__ == "__main__":
    main()
