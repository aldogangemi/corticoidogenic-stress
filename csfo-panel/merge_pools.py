#!/usr/bin/env python3
"""
merge_pools.py — union the item pools from two extractors, tagged by origin.

    python3 merge_pools.py --pool opus5=out_op5/items_real.jsonl \
                           --pool sonnet45=out_s45/items_real.jsonl \
                           --out out/items_real.jsonl

Item ids are content hashes (doc + type + terms), so a claim both extractors made
collapses to one item tagged `both`. The `source` field is kept in items_real.jsonl
for scoring and is STRIPPED from agent payloads by design_panel.py, which only
copies (item_id, item_type, claim, span, doc_id).

Two things this buys:

1. An extractor-agreement baseline. Items tagged `both` are claims two independent
   extractors made from the same text; items tagged with one source are claims only
   one made. That ratio bounds what the panel can be asked -- if most items exist in
   only one pool, adjudication results are not transportable across extractors.

2. A SELF-PREFERENCE TEST. When a backbone that produced one of the extractions also
   serves as an adjudicator, it is grading its own output. Blinded to origin, its
   verdict distribution on same-source vs other-source items should be identical.
   Any gap is a self-preference coefficient, and it is exactly the correlated error
   that makes Dawid-Skene confidently wrong. Report it before trusting any posterior.
"""
from __future__ import annotations
import argparse, json
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", action="append", required=True,
                    help="name=path/to/items_real.jsonl (repeatable)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    merged = {}
    per_pool = Counter()
    for spec in a.pool:
        name, path = spec.split("=", 1)
        for line in open(path):
            it = json.loads(line)
            per_pool[name] += 1
            prev = merged.get(it["item_id"])
            if prev is None:
                it["source"] = [name]
                merged[it["item_id"]] = it
            else:
                if name not in prev["source"]:
                    prev["source"].append(name)
                # keep the union of defect flags; a defect found by either stands
                prev["flags"] = sorted(set(prev.get("flags", [])) | set(it.get("flags", [])))

    for it in merged.values():
        it["source"] = "both" if len(it["source"]) > 1 else it["source"][0]

    with open(a.out, "w") as fh:
        for it in merged.values():
            fh.write(json.dumps(it) + "\n")

    src = Counter(i["source"] for i in merged.values())
    print("items per pool  :", dict(per_pool))
    print("merged unique   :", len(merged))
    for k, v in sorted(src.items()):
        print(f"  {k:<12}{v:>6}  {v/len(merged):>6.1%}")
    both = src.get("both", 0)
    print(f"\nextractor overlap (Jaccard over items): {both/len(merged):.2f}")
    by_type = {}
    for it in merged.values():
        d = by_type.setdefault(it["item_type"], Counter())
        d[it["source"]] += 1
    print("\noverlap by item type:")
    for t in sorted(by_type):
        d = by_type[t]
        n = sum(d.values())
        print(f"  {t:<20} n={n:<6} both={d.get('both',0)/n:>6.1%}")


if __name__ == "__main__":
    main()
