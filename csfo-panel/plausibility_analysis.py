#!/usr/bin/env python3
"""
plausibility_analysis.py — pre-registered analysis of the forced-plausibility fold.

    python3 plausibility_analysis.py --out out \
        --forced out/verdicts.L1.r1.f9.jsonl \
        --baseline out/verdicts.L1.r1.f0.jsonl

Written BEFORE the run. Three hypotheses, each with its falsifier stated.

H0 (compliance). Forcing a rating removes abstention without destroying the
    signal. FALSIFIED IF: OUT_OF_SCOPE stays above 25%, or DISTANT plausibility
    piles onto the midpoint (>60% of DISTANT ratings equal to 3), which would mean
    judges complied nominally while declining to commit.

H1 (informativeness). Out-of-stratum plausibility carries information about the
    item beyond noise: DISTANT ratings correlate with the in-stratum verdict on
    the same item. FALSIFIED IF: Spearman |rho| < 0.1 between mean DISTANT
    plausibility and the in-stratum verdict coded -1/0/+1.

H2 (correlated prior) -- THE ONE THAT MATTERS. If forced out-of-stratum judgements
    are reads of general plausibility rather than of the evidence, judges will
    agree with EACH OTHER more when distant than when competent, because they share
    a training distribution. Dawid-Skene would then read that agreement as truth.
    CONFIRMED IF: pairwise agreement among DISTANT raters significantly exceeds
    agreement among OWN raters (Fisher z, one-sided, alpha=.05).

    Power at n=402 items, one fold: 86% to detect rho_distant=0.35 against
    rho_own=0.15; 37% at 0.25. So a null result bounds the effect below ~0.35
    excess correlation; it does not establish independence.

H2 is the decision rule. If confirmed, forced out-of-stratum ratings must NOT be
pooled into the evidential aggregation, whatever H1 says -- informativeness and
independence are different properties, and the aggregation needs the second.
"""
from __future__ import annotations
import argparse, json, math, os
from collections import Counter, defaultdict
import numpy as np

VMAP = {"SUPPORTED": 1, "UNDERDETERMINED": 0, "CONTRADICTED": -1}


def load(path):
    rows = []
    for l in open(path):
        rows.append(json.loads(l))
    return rows


def fisher_z_test(r1, n1, r2, n2):
    """One-sided: is r2 > r1?"""
    if min(n1, n2) < 10 or abs(r1) >= 1 or abs(r2) >= 1:
        return float("nan"), float("nan")
    z = (np.arctanh(r2) - np.arctanh(r1)) / math.sqrt(1 / (n1 - 3) + 1 / (n2 - 3))
    from math import erfc
    return float(z), float(0.5 * erfc(z / math.sqrt(2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forced", required=True)
    ap.add_argument("--baseline", help="a default-schema fold, for the abstention contrast")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    rows = load(a.forced)
    items = {json.loads(l)["item_id"]: json.loads(l)
             for l in open(os.path.join(a.out, "items.jsonl"))}

    # ---------------- H0 compliance
    n = len(rows)
    oos = sum(1 for r in rows if r["verdict"] == "OUT_OF_SCOPE")
    rated = [r for r in rows if r.get("plausibility") in (1, 2, 3, 4, 5)]
    comp = Counter(r.get("competence", "?") for r in rows)
    print("=== H0 compliance ===")
    print(f"rows {n}   with a plausibility rating {len(rated)} ({len(rated)/n:.0%})   "
          f"OUT_OF_SCOPE {oos/n:.1%}")
    print("declared competence:", {k: f"{v} ({v/n:.0%})" for k, v in comp.most_common()})
    for tier in ("OWN", "ADJACENT", "DISTANT"):
        sub = [r["plausibility"] for r in rated if r.get("competence") == tier]
        if sub:
            mid = sum(1 for x in sub if x == 3) / len(sub)
            print(f"  {tier:<9} n={len(sub):<5} mean {np.mean(sub):.2f}  "
                  f"sd {np.std(sub):.2f}  midpoint share {mid:.0%}"
                  + ("   << MIDPOINT PILE-UP" if mid > 0.60 else ""))
    if a.baseline:
        b = load(a.baseline)
        boos = sum(1 for r in b if r["verdict"] == "OUT_OF_SCOPE") / len(b)
        print(f"baseline fold abstention {boos:.1%}  ->  forced fold {oos/n:.1%}")
    verdict = "PASS" if oos / n <= 0.25 else "FAIL"
    print(f"H0: {verdict}")

    # ---------------- H1 informativeness
    print("\n=== H1 informativeness of DISTANT ratings ===")
    by_item = defaultdict(list)
    for r in rows:
        by_item[r["item_id"]].append(r)
    xs, ys = [], []
    for iid, rs in by_item.items():
        dist = [r["plausibility"] for r in rs
                if r.get("competence") == "DISTANT" and r.get("plausibility")]
        own = [VMAP[r["verdict"]] for r in rs
               if r.get("competence") == "OWN" and r["verdict"] in VMAP]
        if dist and own:
            xs.append(np.mean(dist))
            ys.append(np.mean(own))
    if len(xs) >= 20:
        from scipy.stats import spearmanr
        rho, p = spearmanr(xs, ys)
        print(f"items with both DISTANT rating and OWN verdict: {len(xs)}")
        print(f"Spearman rho = {rho:+.3f}  p = {p:.3g}   "
              f"H1: {'PASS' if abs(rho) >= 0.1 else 'FAIL'}")
    else:
        print(f"only {len(xs)} items have both -- underpowered, report as inconclusive")

    # ---------------- H2 correlated prior
    print("\n=== H2 correlated prior (the decision rule) ===")
    def pair_corr(tier):
        A, B = [], []
        for iid, rs in by_item.items():
            v = [r["plausibility"] for r in rs
                 if r.get("competence") == tier and r.get("plausibility")]
            if len(v) >= 2:
                A.append(v[0]); B.append(v[1])
        if len(A) < 15:
            return float("nan"), len(A)
        return float(np.corrcoef(A, B)[0, 1]), len(A)
    r_own, n_own = pair_corr("OWN")
    r_dist, n_dist = pair_corr("DISTANT")
    print(f"pairwise rating agreement, OWN     : r={r_own:+.3f}  (n={n_own} items)")
    print(f"pairwise rating agreement, DISTANT : r={r_dist:+.3f}  (n={n_dist} items)")
    z, p = fisher_z_test(r_own, n_own, r_dist, n_dist)
    if not math.isnan(z):
        print(f"Fisher z = {z:+.2f}, one-sided p = {p:.3g}")
        if p < 0.05:
            print("H2 CONFIRMED: distant judges agree with each other MORE than competent\n"
                  "  ones do. Their agreement reflects a shared prior, not the evidence.\n"
                  "  DECISION: do not pool forced out-of-stratum ratings into the\n"
                  "  evidential aggregation. Keep abstention.")
        else:
            print("H2 not confirmed at this power. Excess correlation is bounded below\n"
                  "  ~0.35; this does NOT establish independence. Report the bound, and\n"
                  "  if pooling, fit a separate confusion matrix per competence tier.")
    else:
        print("too few paired ratings per tier -- inconclusive")


if __name__ == "__main__":
    main()
