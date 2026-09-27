#!/usr/bin/env python3
"""
run_chain.py — one entry point for the whole CSFO adjudication chain.

    # see the design and the power calculation without running anything
    python3 run_chain.py --plan

    # full run, fold count chosen by power analysis
    python3 run_chain.py --models models.json --auto-folds

    # or fix it yourself
    python3 run_chain.py --models models.json --folds 10

    # resume after an interruption (completed folds are skipped)
    python3 run_chain.py --models models.json --folds 10 --resume

Stages, in order:
  1 strata        derive csf:atStratum by query, dump the materialisation
  2 build         generate 15 agent contexts (5 roles x L0/L1/L2) from the ontology
  3 ingest        each extractor pool -> typed items + defect report
  4 merge         union the pools, tag origin, strip it from payloads
  5 design        anchors, red-team, incomplete block assignment
  6 panel         F folds x (L1 r1, L1 r2, L2 r1) x 5 agents
  7 aggregate     stratified Dawid-Skene, per fold and pooled

--- WHAT ROTATES, AND WHAT DOES NOT ---

The five ROLES are fixed and ontology-derived: each owns a stratum set
(A1 L1, A2 L2+L3, A3 L4+L5, A4 L6, A5 L7), gets a context generated from the
terms the ontology assigns to those strata via csf:atStratum, and receives only
the items routed to it by design_panel.AGENT_STRATA. Rotation never touches any
of that.

What rotates is which BACKBONE plays which role. Fold f assigns
models[(i + f) mod 5] to role i -- a cyclic Latin square of order 5. Over any 5
consecutive folds every model occupies every role exactly once, so model effect
and role effect are orthogonal and separable. Rotating fewer than 5 folds leaves
them confounded, which is the failure the whole panel design exists to avoid.

--- HOW MANY FOLDS ---

Not 41. The identifiable unit is the (model, role) cell: 5 models x 5 roles = 25
cells, covered exactly once per 5-fold square, so folds should be a MULTIPLE OF 5
and F = 5R gives R independent observations per cell.

R is set by what you want to detect, not by the corpus size. --auto-folds solves
for the smallest R such that the per-cell sample supports the target contrasts at
the requested power. The binding constraint is normally the self-preference gap
(SUPPORTED rate on own-extraction vs other-extraction items), because it is
estimated WITHIN a cell and cannot borrow strength across roles.

41 x 5 = 205 folds would be ~1.8M model calls and buys nothing after the variance
components stabilise: every model already sees items from all 41 documents in
every single fold, since payload routing is by stratum, not by document.
"""
from __future__ import annotations
import argparse, itertools, json, math, os, subprocess, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROLES = ["A1_SOCIAL", "A2_PSYCH", "A3_ENDOCRINE", "A4_NEUROIMMUNE", "A5_MOLECULAR"]
ARMS = [("L1", 1), ("L1", 2), ("L2", 1)]        # default; override with --arms


# ------------------------------------------------------------------ power
def n_two_proportions(p, delta, alpha=0.05, power=0.80):
    """Sample size per group for a two-proportion z test."""
    z_a, z_b = 1.959964, 0.8416212
    p1, p2 = p, min(max(p + delta, 1e-6), 1 - 1e-6)
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar)) +
           z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / delta ** 2)


def plan_folds(assignment, items, delta, base_rate, alpha, power, max_r=8):
    """Smallest R (folds = 5R) giving the target power on the binding contrast."""
    per_cell_src = defaultdict(Counter)          # role -> origin -> items per fold
    for iid, roles in assignment.items():
        src = items[iid].get("source", "?")
        for r in roles:
            per_cell_src[r][src] += 1
    need = n_two_proportions(base_rate, delta, alpha, power)

    rows = []
    for role in ROLES:
        c = per_cell_src[role]
        # own-source vs other-source: `both` items are uninformative for this
        # contrast and are excluded from the effective n
        smaller = min(c.get("opus5", 0), c.get("sonnet45", 0))
        rows.append((role, c.get("opus5", 0), c.get("sonnet45", 0), c.get("both", 0), smaller))
    binding = min(r[4] for r in rows)
    R = max(1, math.ceil(need / max(1, binding)))
    R = min(R, max_r)
    return dict(need_per_group=need, binding_cell_n=binding, R=R, folds=5 * R, rows=rows,
                delta=delta, base_rate=base_rate, alpha=alpha, power=power)


# ------------------------------------------------------------------ rotation
def rotation(models, folds):
    """Cyclic Latin square of order 5, repeated. fold f: role i <- models[(i+f)%5]."""
    return [{ROLES[i]: models[(i + f) % len(ROLES)] for i in range(len(ROLES))}
            for f in range(folds)]


def check_balance(schedule):
    cells = Counter()
    for asg in schedule:
        for role, m in asg.items():
            cells[(role, m["model"])] += 1
    counts = set(cells.values())
    return len(cells), counts


# ------------------------------------------------------------------ shell
KEEP_GOING = False
FAILURES = []


def sh(cmd, dry=False):
    print("  $ " + " ".join(cmd), flush=True)
    if dry:
        return 0
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        if KEEP_GOING:
            FAILURES.append(" ".join(cmd))
            print(f"  !! FAILED (continuing): {' '.join(cmd)}", flush=True)
            return r.returncode
        sys.exit(f"stage failed: {' '.join(cmd)}")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology", default="../csfo3/ontology")
    ap.add_argument("--corpus", default="../lj/ljcorpus")
    ap.add_argument("--pool", action="append", default=[],
                    help="name=dir of extractor JSON (repeatable). "
                         "default: opus5 and sonnet45 under ../v3")
    ap.add_argument("--out", default="out")
    ap.add_argument("--models", help="JSON list of 5 {provider, model} in rotation order")
    ap.add_argument("--folds", type=int)
    ap.add_argument("--auto-folds", action="store_true")
    ap.add_argument("--delta", type=float, default=0.05,
                    help="self-preference gap to detect (default 0.05)")
    ap.add_argument("--base-rate", type=float, default=0.30)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.80)
    ap.add_argument("--sample", type=int,
                    help="stratified subsample of the item pool (by item_type x "
                         "source x anchor status). Use for a fast, cheap pilot run; "
                         "anchors and red-team items are kept at their pool rate so "
                         "the specificity and self-preference estimates stay valid.")
    ap.add_argument("--arms", default="L1:1",
                    help="comma list of depth:round, e.g. 'L1:1' (minimal) or "
                         "'L1:1,L1:2,L2:1' (full). Default minimal.")
    ap.add_argument("--plan", action="store_true", help="print the design and exit")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--keep-going", action="store_true",
                    help="do not abort the run on a failed fold; report at the end")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-prep", action="store_true",
                    help="stages 1-5 already done; go straight to the folds")
    a = ap.parse_args()

    global KEEP_GOING
    KEEP_GOING = a.keep_going
    pools = a.pool or ["opus5=../v3/op5/lightweight_out_bridged",
                       "sonnet45=../v3/s45/lightweight_out_bridged_sonnet45"]

    # ---------------- stages 1-5
    if not a.skip_prep:
        print("\n[1/7] strata")
        sh(["python3", "strata.py", "--ontology", a.ontology,
            "--dump", f"{a.out}/atStratum_materialised.nt"], a.dry_run)
        print("\n[2/7] contexts")
        sh(["python3", "build_panel.py", "--ontology", a.ontology, "--out", a.out], a.dry_run)
        merged = []
        for i, spec in enumerate(pools):
            name, d = spec.split("=", 1)
            sub = f"{a.out}_{name}"
            print(f"\n[3/7] ingest {name}")
            sh(["python3", "ingest_lightweight.py", "--extractions", d,
                "--corpus", a.corpus, "--ontology", a.ontology, "--out", sub], a.dry_run)
            merged += ["--pool", f"{name}={sub}/items_real.jsonl"]
        print("\n[4/7] merge")
        sh(["python3", "merge_pools.py", *merged, "--out", f"{a.out}/items_real.jsonl"], a.dry_run)
        print("\n[5/7] design")
        sh(["python3", "design_panel.py", "--extractions", f"{a.out}/items_real.jsonl",
            "--corpus", a.corpus, "--out", a.out], a.dry_run)

    # ---------------- fold count
    items_path = os.path.join(HERE, a.out, "items.jsonl")
    asg_path = os.path.join(HERE, a.out, "assignment.json")
    if not (os.path.exists(items_path) and os.path.exists(asg_path)):
        sys.exit("run the prep stages first (drop --skip-prep)")
    items = {json.loads(l)["item_id"]: json.loads(l) for l in open(items_path)}
    assignment = json.load(open(asg_path))["assignment"]

    arms = [(d, int(r)) for d, r in (x.split(":") for x in a.arms.split(","))]
    if a.sample and a.sample < len(items):
        import random as _r
        rng = _r.Random(17)
        buckets = defaultdict(list)
        for iid, it in items.items():
            key = (it["item_type"], it.get("source", "?"),
                   it.get("kind", "extracted"), iid in json.load(open(asg_path))["anchors"])
            buckets[key].append(iid)
        frac = a.sample / len(items)
        keep = set()
        for k, v in buckets.items():
            rng.shuffle(v)
            keep |= set(v[: max(1, round(len(v) * frac))])
        items = {k: v for k, v in items.items() if k in keep}
        assignment = {k: v for k, v in assignment.items() if k in keep}
        # rebuild payloads from items+assignment (NOT by filtering the existing
        # payload files, which would compound across re-runs)
        PUBLIC = ("item_id", "item_type", "claim", "span", "doc_id")
        full = {json.loads(l)["item_id"]: json.loads(l) for l in open(items_path)}
        for role in ROLES:
            p = os.path.join(HERE, a.out, "payloads", f"{role}.jsonl")
            with open(p, "w") as fh:
                for iid in keep:
                    if role in assignment.get(iid, []):
                        it = full[iid]
                        fh.write(json.dumps({k: it.get(k, "") for k in PUBLIC}) + "\n")
        print(f"\nSAMPLED pool: {len(items)} items "
              f"({sum(len(v) for v in assignment.values())} judgments per fold)")

    plan = plan_folds(assignment, items, a.delta, a.base_rate, a.alpha, a.power)
    folds = a.folds or (plan["folds"] if a.auto_folds else 5)
    if folds % 5:
        print(f"WARNING folds={folds} is not a multiple of 5: the Latin square is "
              f"incomplete and model effect stays partly confounded with role effect.")

    per_fold = sum(len(v) for v in assignment.values())
    print("\n" + "=" * 72)
    print("DESIGN")
    print("=" * 72)
    print(f"items {len(items)}   judgments per fold {per_fold}   arms per fold {len(arms)}")
    print(f"\npower target: detect a self-preference gap of {a.delta:+.2f} around "
          f"p={a.base_rate:.2f} at alpha={a.alpha}, power={a.power}")
    print(f"  required n per group          : {plan['need_per_group']}")
    print(f"  smallest per-fold cell (role) : {plan['binding_cell_n']}")
    print(f"  -> replications per cell R    : {plan['R']}   (folds = 5R = {plan['folds']})")
    print("\n  per-role items by origin, per fold:")
    print(f"    {'role':<16}{'opus5':>8}{'sonnet45':>10}{'both':>8}{'binding':>9}")
    for role, o, s, b, m in plan["rows"]:
        print(f"    {role:<16}{o:>8}{s:>10}{b:>8}{m:>9}")

    if not a.models:
        print("\nno --models given; showing the design only.")
        print("models.json format: [{\"provider\": \"...\", \"model\": \"...\"}, x5]")
        return
    models = json.load(open(a.models))
    if len(models) != len(ROLES):
        sys.exit(f"--models must list exactly {len(ROLES)} backbones")
    names = [m["model"] for m in models]
    if len(set(names)) < len(names):
        print(f"\nWARNING duplicate backbones {[n for n in names if names.count(n)>1]}: "
              "their errors are correlated and Dawid-Skene will treat them as "
              "independent. Report the duplication with every posterior.")

    schedule = rotation(models, folds)
    ncells, counts = check_balance(schedule)
    print(f"\nrotation: {folds} folds, {ncells}/25 (role,model) cells covered, "
          f"reps per cell {sorted(counts)}")
    total_calls = folds * len(arms) * per_fold / 12
    print(f"estimated model calls: ~{total_calls:,.0f}  "
          f"(batch size 12)")

    json.dump([{r: m["model"] for r, m in s.items()} for s in schedule],
              open(os.path.join(HERE, a.out, "rotation_schedule.json"), "w"), indent=1)
    if a.plan:
        return

    # ---------------- stage 6: folds
    print("\n[6/7] panel")
    for f, asg in enumerate(schedule):
        bb_path = os.path.join(HERE, a.out, f"backbones.f{f}.json")
        json.dump(asg, open(bb_path, "w"), indent=1)
        for depth, rnd in arms:
            tag = f"{depth}.r{rnd}.f{f}"
            outfile = os.path.join(HERE, a.out, f"verdicts.{tag}.jsonl")
            if a.resume and os.path.exists(outfile) and os.path.getsize(outfile) > 0:
                print(f"  fold {f} {depth} r{rnd}: exists, skipping")
                continue
            cmd = ["python3", "run_panel.py", "--out", a.out, "--depth", depth,
                   "--round", str(rnd), "--backbones", bb_path, "--fold", str(f),
                   "--seed", str(1000 + f)]
            if rnd == 2:
                prior = os.path.join(HERE, a.out, f"verdicts.{depth}.r1.f{f}.jsonl")
                if not os.path.exists(prior) and not a.dry_run:
                    print(f"  fold {f} {depth} r2: no round-1 file, skipping")
                    continue
                cmd += ["--prior", prior]
            if a.dry_run:
                cmd.append("--dry-run")
            print(f"  fold {f} [{', '.join(m['model'] for m in [asg[r] for r in ROLES])}]"
                  f" {depth} r{rnd}")
            sh(cmd, False)

    # ---------------- stage 7: aggregate
    print("\n[7/7] aggregate")
    for f in range(folds):
        v1 = os.path.join(a.out, f"verdicts.L1.r1.f{f}.jsonl")
        v2 = os.path.join(a.out, f"verdicts.L2.r1.f{f}.jsonl")
        cmd = ["python3", "to_ds.py", "--verdicts", v1, "--out", a.out,
               "--items", f"{a.out}/items.jsonl", "--assignment", f"{a.out}/assignment.json"]
        if os.path.exists(os.path.join(HERE, v2)):
            cmd += ["--compare", v2]
        sh(cmd, a.dry_run)
    if FAILURES:
        print(f"\n{len(FAILURES)} step(s) failed:")
        for f in FAILURES:
            print("  " + f)
        print("re-run the same command with --resume to fill the gaps.")
    print("\ndone. pool the per-fold fits with variance components before reporting: "
          "role, model, and role x model are now separable by construction.")


if __name__ == "__main__":
    main()
