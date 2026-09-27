#!/usr/bin/env python3
"""
pilot_decorrelate.py — pick the five backbones by MEASURING error correlation,
not by guessing which models are "distant".

    # 1. list candidates (families, vintages, reasoning modes) in candidates.json
    # 2. run each on the anchor subset only -- cheap, and anchors have known labels
    python3 pilot_decorrelate.py --candidates candidates.json --n 250 --out out
    # 3. it writes models.json, ordered for the rotation

Why anchors: 597 items in the merged pool have a known verdict (text-gold
positives, ontology-convicted negatives, red-team calibration). That gives a real
ERROR VECTOR per candidate, so pairwise error correlation is measurable rather
than assumed. Everything downstream depends on this number: Dawid-Skene treats
judges as conditionally independent given the latent verdict, and the effective
panel size is

    N_eff = J / (1 + (J-1) * rho_bar)

With J=5 and rho_bar=0.7 a five-model panel is worth about two independent
judges. Report N_eff with every posterior.

Selection also respects a structural constraint: when two chosen backbones share
a family, they are assigned to the ROLE PAIR that co-judges the fewest items, so
their correlated errors overlap on as little of the pool as possible -- and never
to A3/A4, the L5-L6 boundary carrying the glucocorticoid-resistance question.
"""
from __future__ import annotations
import argparse, itertools, json, os, random
from collections import defaultdict
import numpy as np

ROLES = ["A1_SOCIAL", "A2_PSYCH", "A3_ENDOCRINE", "A4_NEUROIMMUNE", "A5_MOLECULAR"]
FORBIDDEN_SAME_FAMILY = {("A3_ENDOCRINE", "A4_NEUROIMMUNE")}
CLASSES = {"SUPPORTED": 0, "UNDERDETERMINED": 1, "CONTRADICTED": 2}


def error_vectors(pilot_verdicts, anchors, item_ids):
    """{candidate: np.array of 0/1 errors over item_ids}, NaN where abstained."""
    out = {}
    for cand, per_item in pilot_verdicts.items():
        v = np.full(len(item_ids), np.nan)
        for k, iid in enumerate(item_ids):
            r = per_item.get(iid)
            if r and r in CLASSES:
                v[k] = 0.0 if r == anchors[iid] else 1.0
        out[cand] = v
    return out


def pairwise_rho(errs):
    names = sorted(errs)
    n = len(names)
    R = np.full((n, n), np.nan)
    for i, j in itertools.combinations(range(n), 2):
        a, b = errs[names[i]], errs[names[j]]
        m = ~np.isnan(a) & ~np.isnan(b)
        if m.sum() < 30 or a[m].std() == 0 or b[m].std() == 0:
            continue
        R[i, j] = R[j, i] = float(np.corrcoef(a[m], b[m])[0, 1])
    np.fill_diagonal(R, 1.0)
    return names, R


def n_eff(rho_bar, J):
    """Effective panel size. Clamped at J: a negative sample rho_bar means the
    candidates are indistinguishable from independent at this sample size, not
    that you have more than J independent judges."""
    d = 1 + (J - 1) * max(rho_bar, 0.0)
    return min(J, J / d) if d > 0 else float("nan")


def choose(names, R, families, k=5):
    """Exhaustive over C(n,5): minimise mean pairwise error correlation."""
    idx = range(len(names))
    best = None
    for combo in itertools.combinations(idx, k):
        vals = [R[i, j] for i, j in itertools.combinations(combo, 2)]
        vals = [v for v in vals if not np.isnan(v)]
        if len(vals) < k * (k - 1) / 2 * 0.6:
            continue
        m = float(np.mean(vals))
        if best is None or m < best[0]:
            best = (m, combo)
    return best


def assign_roles(chosen, families, pair_overlap):
    """Same-family pairs go on the lowest-overlap role pairs; never on A3/A4."""
    best = None
    for perm in itertools.permutations(chosen):
        role_of = dict(zip(ROLES, perm))
        cost, bad = 0.0, False
        for r1, r2 in itertools.combinations(ROLES, 2):
            if families[role_of[r1]] == families[role_of[r2]]:
                if tuple(sorted((r1, r2))) in {tuple(sorted(p)) for p in FORBIDDEN_SAME_FAMILY}:
                    bad = True
                    break
                cost += pair_overlap.get(f"{min(r1,r2)}|{max(r1,r2)}", 0)
        if bad:
            continue
        if best is None or cost < best[0]:
            best = (cost, role_of)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True,
                    help='JSON list of {"name","provider","model","family",'
                         '"thinking":bool}')
    ap.add_argument("--out", default="out")
    ap.add_argument("--n", type=int, default=250, help="anchor items to pilot on")
    ap.add_argument("--pilot-verdicts",
                    help="pre-collected {candidate: {item_id: verdict}} JSON; "
                         "if absent, run run_panel.py per candidate first")
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()

    cands = json.load(open(a.candidates))
    families = {c["name"]: c["family"] for c in cands}
    asg = json.load(open(os.path.join(a.out, "assignment.json")))
    anchors = {k: v for k, v in asg["anchors"].items() if v in CLASSES}
    rng = random.Random(a.seed)
    ids = sorted(anchors)
    rng.shuffle(ids)
    ids = ids[: a.n]
    json.dump(ids, open(os.path.join(a.out, "pilot_items.json"), "w"), indent=1)

    if not a.pilot_verdicts:
        print(f"pilot set written: {len(ids)} anchored items -> {a.out}/pilot_items.json")
        print("collect verdicts for each candidate, then re-run with --pilot-verdicts.\n"
              "candidates:")
        for c in cands:
            print(f"  {c['name']:<22}{c['family']:<12}{c['provider']}/{c['model']}")
        print(f"\ncost: {len(cands)} x {len(ids)} items = "
              f"{len(cands)*len(ids)} judgments, ~{len(cands)*len(ids)/12:.0f} calls")
        return

    pv = json.load(open(a.pilot_verdicts))
    errs = error_vectors(pv, anchors, ids)
    names, R = pairwise_rho(errs)

    print("accuracy on anchors (competence, for context only):")
    for n in names:
        v = errs[n]
        m = ~np.isnan(v)
        print(f"  {n:<22}{1-v[m].mean():.3f}   judged {int(m.sum())}/{len(ids)}")

    print("\npairwise ERROR correlation (this is what decides the panel):")
    print("      " + "".join(f"{n[:8]:>10}" for n in names))
    for i, n in enumerate(names):
        row = "".join(f"{R[i,j]:>10.2f}" if not np.isnan(R[i, j]) else f"{'--':>10}"
                      for j in range(len(names)))
        print(f"  {n[:8]:<6}{row}")

    fam = defaultdict(list)
    for n in names:
        fam[families[n]].append(n)
    print("\nwithin-family vs cross-family mean error correlation:")
    wi = [R[i, j] for i, j in itertools.combinations(range(len(names)), 2)
          if families[names[i]] == families[names[j]] and not np.isnan(R[i, j])]
    cr = [R[i, j] for i, j in itertools.combinations(range(len(names)), 2)
          if families[names[i]] != families[names[j]] and not np.isnan(R[i, j])]
    if wi:
        print(f"  within-family : {np.mean(wi):.3f}  (n={len(wi)} pairs)")
    if cr:
        print(f"  cross-family  : {np.mean(cr):.3f}  (n={len(cr)} pairs)")

    best = choose(names, R, families)
    if not best:
        print("\nnot enough overlap to choose; raise --n")
        return
    rho_bar, combo = best
    chosen = [names[i] for i in combo]
    print(f"\nselected panel (mean pairwise error rho = {rho_bar:.3f}):")
    for n in chosen:
        print(f"  {n:<22}{families[n]}")
    print(f"  effective panel size N_eff = {n_eff(rho_bar,5):.2f} of 5"
          + ("   [rho_bar < 0: consistent with independence at this n; "
             "widen --n before trusting it]" if rho_bar < 0 else ""))

    ra = assign_roles(chosen, families, asg.get("pair_overlap", {}))
    if ra:
        cost, role_of = ra
        print(f"\nrole assignment (same-family overlap cost {cost:.0f} items):")
        by_name = {c["name"]: c for c in cands}
        models = []
        for r in ROLES:
            c = by_name[role_of[r]]
            print(f"  {r:<16}{c['name']:<22}{c['family']}")
            models.append({"provider": c["provider"], "model": c["model"],
                           "family": c["family"], "candidate": c["name"]})
        json.dump(models, open(os.path.join(a.out, "models.json"), "w"), indent=1)
        json.dump(dict(rho_bar=rho_bar, n_eff=n_eff(rho_bar, 5), chosen=chosen,
                       families={n: families[n] for n in chosen},
                       matrix={names[i]: {names[j]: (None if np.isnan(R[i, j]) else R[i, j])
                                          for j in range(len(names))}
                               for i in range(len(names))}),
                  open(os.path.join(a.out, "panel_correlation.json"), "w"), indent=1)
        print(f"\n-> {a.out}/models.json (rotation order)  "
              f"and {a.out}/panel_correlation.json")


if __name__ == "__main__":
    main()
