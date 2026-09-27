#!/usr/bin/env python3
"""
to_ds.py — verdicts -> stratified Dawid-Skene, with the diagnostics that decide
whether the DS output is usable at all.

    python3 to_ds.py --verdicts out/verdicts.L1.r1.jsonl
    python3 to_ds.py --verdicts out/verdicts.L1.r1.jsonl --compare out/verdicts.L2.r1.jsonl

Why stratified: DS assumes ONE confusion matrix per agent across all items. Role
fillers and cross-layer bridges are not the same task, so a single fit lets the
bridges' difficulty contaminate the within-layer competence estimates. Each item
type gets its own fit; the bridge strata (T5/T6) have no anchors by construction
and are shrunk toward the anchored strata rather than fit free.

OUT_OF_SCOPE is written as missing (-1), never as a class. Abstention is not a
verdict; treating it as one turns incompetence into uncertainty.
"""
from __future__ import annotations
import argparse, json, os
from collections import defaultdict, Counter
import numpy as np
from ds_csfo import dawid_skene, majority_vote

CLASS = {"SUPPORTED": 0, "UNDERDETERMINED": 1, "CONTRADICTED": 2}
AGENTS = ["A1_SOCIAL", "A2_PSYCH", "A3_ENDOCRINE", "A4_NEUROIMMUNE", "A5_MOLECULAR"]
ANCHORED = ("T1_FRAME", "T2_ROLE", "T3_MODERATOR", "T4_STEP")
UNANCHORED = ("T5_BRIDGE", "T6_OUTCOME_BRIDGE")


def load(verdicts_path, items_path, assignment_path):
    items = {}
    for l in open(items_path):
        it = json.loads(l)
        items[it["item_id"]] = it
    asg = json.load(open(assignment_path))
    V = defaultdict(dict)
    for l in open(verdicts_path):
        r = json.loads(l)
        V[r["item_id"]][r["agent"]] = r
    return items, asg, V


def matrix(ids, V):
    L = -np.ones((len(ids), len(AGENTS)), dtype=int)
    for i, iid in enumerate(ids):
        for j, a in enumerate(AGENTS):
            r = V.get(iid, {}).get(a)
            if r and r["verdict"] in CLASS:      # OUT_OF_SCOPE -> stays missing
                L[i, j] = CLASS[r["verdict"]]
    return L


def shrink(Pi_free, Pi_anchor, w):
    """Partial pooling of the unanchored stratum toward the anchored estimate."""
    return w * Pi_anchor + (1 - w) * Pi_free


def run(items, asg, V, args):
    anchors_all = asg.get("anchors", {})
    by_type = defaultdict(list)
    for iid, it in items.items():
        by_type[it["item_type"]].append(iid)

    fits = {}
    print(f"{'stratum':<20}{'n':>6}{'judged':>8}{'anchors':>9}   posterior>=0.9")
    for t in ANCHORED + UNANCHORED:
        ids = sorted(by_type.get(t, []))
        if not ids:
            continue
        L = matrix(ids, V)
        judged = int((L >= 0).any(1).sum())
        anc = {ids.index(i): CLASS[v] for i, v in anchors_all.items()
               if i in ids and v in CLASS}
        T, Pi, p = dawid_skene(L, anchors=anc or None)
        fits[t] = dict(ids=ids, T=T, Pi=Pi, p=p, L=L)
        conf = T.max(1)
        print(f"{t:<20}{len(ids):>6}{judged:>8}{len(anc):>9}   "
              f"{(conf>=0.9).mean():>6.0%}")

    # shrink the unanchored bridge strata toward the anchored consensus profile
    anchored_Pi = [fits[t]["Pi"] for t in ANCHORED if t in fits]
    if anchored_Pi:
        Pi_bar = np.mean(anchored_Pi, axis=0)
        for t in UNANCHORED:
            if t not in fits:
                continue
            n_eff = (fits[t]["L"] >= 0).sum()
            w = float(args.shrink_w if args.shrink_w is not None
                      else 200.0 / (200.0 + n_eff))     # more data -> less pooling
            Pi_s = shrink(fits[t]["Pi"], Pi_bar, w)
            T2, _, _ = dawid_skene(fits[t]["L"], anchors=None)
            fits[t]["Pi_shrunk"], fits[t]["shrink_w"] = Pi_s, w
            print(f"  {t}: shrinkage weight toward anchored profile = {w:.2f}"
                  f"  (n judgments = {n_eff})")

    # ---- red-team specificity: the one metric you can compute without truth
    prov_path = os.path.join(args.out, "anchor_provenance.json")
    prov = json.load(open(prov_path)) if os.path.exists(prov_path) else {}
    # score specificity ONLY on the held-out half: the calibration half is
    # anchored to CONTRADICTED, so scoring it would be circular.
    rt = [i for i, it in items.items()
          if it.get("kind") == "redteam" and prov.get(i) != "C_redteam_calib"]
    if rt:
        caught = 0
        for t, f in fits.items():
            idx = {iid: k for k, iid in enumerate(f["ids"])}
            for iid in rt:
                if iid in idx:
                    k = idx[iid]
                    if f["T"][k].argmax() == CLASS["CONTRADICTED"]:
                        caught += 1
        n_calib = sum(1 for v in prov.values() if v == "C_redteam_calib")
        print(f"\nred-team held out    : {len(rt)}  (calibration half {n_calib} excluded)")
        print(f"panel rejection rate : {caught/len(rt):.2%}   "
              f"(specificity estimate; unrejected defective items are the "
              f"panel's confabulation floor)")
        wf = defaultdict(int)
        for iid in rt:
            for a, r in V.get(iid, {}).items():
                wf[r["wellformed"]] += 1
        print(" well-formedness calls on red-team items:", dict(wf))

    # ---- per-agent estimated competence, anchored strata only
    print("\nestimated per-agent competence (diagonal of Pi, anchored strata):")
    for j, a in enumerate(AGENTS):
        d = np.mean([np.diag(fits[t]["Pi"][j]) for t in ANCHORED if t in fits], axis=0)
        print(f"  {a:<16} SUP={d[0]:.2f}  UND={d[1]:.2f}  CON={d[2]:.2f}")

    # ---- the unresolved queue: this is the deliverable, not the failures
    queue = []
    for t, f in fits.items():
        for k, iid in enumerate(f["ids"]):
            conf = f["T"][k].max()
            obs = f["L"][k][f["L"][k] >= 0]
            if len(obs) >= 2 and conf < args.queue_threshold and len(set(obs)) > 1:
                queue.append(dict(item_id=iid, item_type=t, posterior=float(conf),
                                  verdicts=[int(x) for x in obs],
                                  claim=items[iid]["claim"],
                                  defeaters=[r["defeater"] for r in V.get(iid, {}).values()
                                             if r.get("defeater")]))
    queue.sort(key=lambda d: d["posterior"])
    json.dump(queue, open(os.path.join(args.out, "bridge_debug_queue.json"), "w"), indent=1)
    print(f"\nunresolved queue     : {len(queue)} items "
          f"({sum(1 for q in queue if q['item_type'] in UNANCHORED)} of them bridges)"
          f" -> out/bridge_debug_queue.json")
    return fits


def self_preference(items, V, out_dir):
    """Does a backbone judge items from its own extractor more leniently?

    Agents are blinded to origin, so any systematic gap between same-source and
    other-source items is self-preference -- the correlated error that breaks the
    Dawid-Skene conditional-independence assumption. Set EXTRACTOR_BACKBONE to the
    agents whose backbone also produced an extraction.
    """
    import os
    bb = json.load(open("backbones.json")) if os.path.exists("backbones.json") else {}
    rows = []
    for a in AGENTS:
        model = (bb.get(a) or {}).get("model", "")
        by_src = defaultdict(Counter)
        for iid, per in V.items():
            r = per.get(a)
            if not r or r["verdict"] == "OUT_OF_SCOPE":
                continue
            by_src[items[iid].get("source", "?")][r["verdict"]] += 1
        def rate(c):
            n = sum(c.values())
            return (c["SUPPORTED"] / n if n else float("nan")), n
        o, no = rate(by_src.get("opus5", Counter()))
        s, ns = rate(by_src.get("sonnet45", Counter()))
        rows.append((a, model, o, no, s, ns, o - s))
    print("\nself-preference probe (SUPPORTED rate by item origin; agents are blinded):")
    print(f"  {'agent':<16}{'backbone':<18}{'opus5':>8}{'n':>7}{'sonnet45':>10}{'n':>7}{'gap':>8}")
    for a, m, o, no, s, ns, g in rows:
        print(f"  {a:<16}{m:<18}{o:>8.3f}{no:>7}{s:>10.3f}{ns:>7}{g:>+8.3f}")
    print("  a backbone that also produced an extraction should show gap ~ 0;"
          " a positive gap toward its own output is self-preference.")
    json.dump([dict(agent=a, model=m, opus5=o, n_opus5=no, sonnet45=s,
                    n_sonnet45=ns, gap=g) for a, m, o, no, s, ns, g in rows],
              open(os.path.join(out_dir, "self_preference.json"), "w"), indent=1)


def compare_depths(a_path, b_path, items):
    """Projection-depth probe: items whose agreement JUMPS when bridge axioms are
    supplied are candidate ontology-manufactured agreement -- exactly the
    correlated error that makes DS confidently wrong. Hold them out."""
    def agree(path):
        V = defaultdict(dict)
        for l in open(path):
            r = json.loads(l)
            V[r["item_id"]][r["agent"]] = r["verdict"]
        out = {}
        for iid, d in V.items():
            v = [x for x in d.values() if x != "OUT_OF_SCOPE"]
            if len(v) >= 2:
                out[iid] = max(v.count(x) for x in set(v)) / len(v)
        return out
    A, B = agree(a_path), agree(b_path)
    shared = sorted(set(A) & set(B))
    lift = [(B[i] - A[i], i) for i in shared]
    flagged = [i for d, i in lift if d >= 0.34]
    print(f"\ndepth probe          : {len(shared)} items judged at both depths")
    print(f"  mean agreement L1 -> L2 : {np.mean([A[i] for i in shared]):.3f} -> "
          f"{np.mean([B[i] for i in shared]):.3f}")
    print(f"  agreement-lift flagged  : {len(flagged)} items "
          f"({len(flagged)/max(1,len(shared)):.1%}) -- exclude from the DS pool")
    json.dump(flagged, open("out/axiom_manufactured.json", "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--compare", help="verdicts at a different projection depth")
    ap.add_argument("--items", default="out/items.jsonl")
    ap.add_argument("--assignment", default="out/assignment.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--shrink-w", type=float, default=None)
    ap.add_argument("--queue-threshold", type=float, default=0.75)
    args = ap.parse_args()

    items, asg, V = load(args.verdicts, args.items, args.assignment)
    run(items, asg, V, args)
    if any("source" in it for it in items.values()):
        self_preference(items, V, args.out)
    if args.compare:
        compare_depths(args.verdicts, args.compare, items)


if __name__ == "__main__":
    main()
