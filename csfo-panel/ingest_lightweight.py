#!/usr/bin/env python3
"""
ingest_lightweight.py — lightweight CSFO extractor JSON -> typed adjudicable items.

    python3 ingest_lightweight.py --extractions ../v2/lw/lightweight_out_bridged \
        --corpus ../lj/ljcorpus --ontology ../csfo2/ontology --out out

Item types (each ONE atomic claim; see README):
  T1_FRAME        primary/secondary/protective frame attribution
  T2_ROLE         ISIT/situational parameter assertions  (isit_csfo)
  T3_MODERATOR    an amplifier or buffer is present
  T4_STEP         a cascade step is instantiated at a stratum
  T5_BRIDGE       cross-stratum step->step or moderator->step influence
  T6_OUTCOME_BRIDGE  terminal step -> L8 outcome

Also emits a defect report: the extractor's own well-formedness problems, which
must be known BEFORE agents judge, because several of them make an item
unjudgeable rather than false.
"""
from __future__ import annotations
import argparse, glob, hashlib, json, os, re
from collections import Counter, defaultdict
from strata import load as load_graph, assign_all, bridge_checks, MODERATOR_ROUTING

STRATUM_LABEL = {
    "L1": "social / situational", "L2": "cognitive / appraisal", "L3": "behavioural",
    "L4": "autonomic", "L5": "neuroendocrine (HPA)", "L6": "immune / inflammatory",
    "L7": "molecular / epigenetic", "L8": "clinical outcome",
}
# text can attest at these strata; below them everything is abduction
ATTESTABLE = {"L1", "L2", "L3"}   # L2 joins now that AppraisalStep exists
NS = {"csc": "http://purl.org/csf/cascades#", "sit": "http://purl.org/csf/situations#",
      "out": "http://purl.org/csf/outcomes#", "mod": "http://purl.org/csf/moderators#",
      "frm": "http://purl.org/csf/frames#", "int": "http://purl.org/csf/interventions#"}


def iid(*p):
    return "I" + hashlib.sha1("|".join(map(str, p)).encode()).hexdigest()[:10]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_known(ont_dir):
    """Declared subjects, and a normalised index of moderator individuals."""
    from rdflib import Graph
    pre = ("@prefix csf: <http://purl.org/csf/> .\n@prefix csc: <http://purl.org/csf/cascades#> .\n"
           "@prefix frm: <http://purl.org/csf/frames#> .\n@prefix mod: <http://purl.org/csf/moderators#> .\n"
           "@prefix out: <http://purl.org/csf/outcomes#> .\n@prefix sit: <http://purl.org/csf/situations#> .\n"
           "@prefix int: <http://purl.org/csf/interventions#> .\n@prefix brg: <http://purl.org/csf/bridging#> .\n"
           "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
           "@prefix dul: <http://www.ontologydesignpatterns.org/ont/dul/DUL.owl#> .\n"
           "@prefix pol: <https://w3id.org/polanyi/core#> .\n")
    g = Graph()
    for f in sorted(glob.glob(os.path.join(ont_dir, "*.ttl"))):
        try:
            g.parse(data=pre + open(f).read(), format="turtle")
        except Exception:
            pass
    known = {str(s) for s in set(g.subjects())}
    modidx = {norm(str(s).split("#")[-1]): "mod:" + str(s).split("#")[-1]
              for s in known if "/moderators#" in str(s)}
    # strata-lift: step type -> YStep class
    lift = {}
    p = os.path.join(ont_dir, "csf-strata-lift-v34.ttl")
    if os.path.exists(p):
        for line in open(p):
            m = re.match(r"^(csc:\S+)\s+rdfs:subClassOf\s+(csf:\S+)\s*\.", line.strip())
            if m:
                lift[m.group(1)] = m.group(2)
    return known, modidx, lift


def declared(curie, known):
    if ":" not in curie:
        return False
    p, l = curie.split(":", 1)
    return NS.get(p, "\0") + l in known


def resolve_moderator(curie_or_text, modidx):
    """amp:/buf: pseudo-namespaces and free-text factors -> mod: individuals."""
    raw = curie_or_text.split(":", 1)[1] if ":" in curie_or_text else curie_or_text
    return modidx.get(norm(raw))


def build(args):
    known, modidx, lift = load_known(args.ontology)
    g = load_graph(args.ontology)
    onto_stratum, idx = assign_all(g)
    def onto_of(curie):
        if ":" not in curie:
            return None
        p, l = curie.split(":", 1)
        hit = onto_stratum.get(NS.get(p, "\0") + l)
        return hit[0] if hit else None
    items, defects = [], Counter()
    stratum_of_step = {}

    docs = sorted(glob.glob(os.path.join(args.extractions, "*.json")),
                  key=lambda p: int(re.search(r"D(\d+)", p).group(1)))
    for path in docs:
        doc = os.path.basename(path)[:-5]
        d = json.load(open(path))
        text = ""
        tp = os.path.join(args.corpus, doc + ".txt")
        if os.path.exists(tp):
            text = open(tp).read()

        # ---------- index the document's steps by stratum
        by_stratum = defaultdict(list)
        for s in d.get("multilayer", []):
            by_stratum[s["layer"]].append(s)
            stratum_of_step.setdefault(s["step"], Counter())[s["layer"]] += 1

        # ---------- T1 frames
        pf = d.get("primaryFrame", {})
        if pf.get("uri"):
            items.append(dict(
                item_id=iid(doc, "T1", pf["uri"]), item_type="T1_FRAME", doc_id=doc,
                claim=f"This account instantiates {pf['uri']} ({pf.get('label','')}).",
                span=d.get("frameVerdict", {}).get("warrant", ""),
                strata=["SCHEMA"], terms=[pf["uri"]], kind="extracted",
                extractor_status=d.get("frameVerdict", {}).get("status", "")))
        for sf in d.get("secondaryFrames", []) + d.get("protectiveFrames", []):
            items.append(dict(
                item_id=iid(doc, "T1s", sf.get("id", ""), sf.get("label", "")),
                item_type="T1_FRAME", doc_id=doc,
                claim=f"A further frame applies to this account: {sf.get('label','')}"
                      f" (extractor id {sf.get('id','?')}).",
                span="", strata=["SCHEMA"], terms=[sf.get("id", "")], kind="extracted"))
            if not sf.get("uri"):
                defects["frame_without_uri"] += 1

        # ---------- T2 situational parameters (ISIT)
        for k, v in (d.get("isit_csfo") or {}).items():
            items.append(dict(
                item_id=iid(doc, "T2", k), item_type="T2_ROLE", doc_id=doc,
                claim=f"For this account, {k} is approximately {v:.2f} on a 0-1 scale.",
                span="", strata=["L1" if k in ("socialEmbeddedness",) else "L2"],
                terms=[k], kind="extracted", value=v))
        if d.get("gasPhase"):
            items.append(dict(
                item_id=iid(doc, "T2g", d["gasPhase"]), item_type="T2_ROLE", doc_id=doc,
                claim=f"This account is in the {d['gasPhase']} phase of the general "
                      f"adaptation syndrome.", span="", strata=["L2"],
                terms=["gasPhase"], kind="extracted"))

        # ---------- T3 moderators
        for m in d.get("moderators", []):
            uri = resolve_moderator(m["factor"], modidx)
            if not uri:
                defects["moderator_unmappable"] += 1
            strat = {"social": "L1", "contextual": "L1", "developmental": "L1",
                     "cognitive": "L2", "behavioral": "L3"}.get(m.get("subtype"), "L2")
            items.append(dict(
                item_id=iid(doc, "T3", norm(m["factor"])), item_type="T3_MODERATOR",
                doc_id=doc,
                claim=f"The {m.get('role','moderator')} '{m['factor']}' "
                      f"({m.get('subtype','?')}) is present in this account, "
                      f"strength {m.get('strength','?')}.",
                span="", strata=[strat], terms=[uri or m["factor"]], kind="extracted",
                resolved=bool(uri)))

        # ---------- T4 steps
        for s in d.get("multilayer", []):
            lay = s["layer"]
            bad = []
            if not declared(s["step"], known):
                bad.append("coined_uri")
                defects["step_coined_uri"] += 1
            if s.get("status") == "Observed" and lay not in ATTESTABLE:
                bad.append("observed_below_L3")
                defects["observed_below_L3"] += 1
            cls = lift.get(s["step"])
            onto = onto_of(s["step"])
            if onto and onto != lay:
                bad.append(f"stratum_conflict:{lay}_vs_{onto}")
                defects[f"stratum_conflict_{lay}_vs_{onto}"] += 1
            items.append(dict(
                item_id=iid(doc, "T4", s["step"], lay), item_type="T4_STEP", doc_id=doc,
                claim=f"The step {s['step']} is instantiated at stratum {lay} "
                      f"({STRATUM_LABEL.get(lay,'?')}), status {s.get('status')}, "
                      f"extractor plausibility {s.get('plausibility')}.",
                span=s.get("cue", ""), strata=[lay], terms=[s["step"]],
                kind="extracted", lift_class=cls, flags=bad))

        # ---------- T5/T6 bridges
        for b in d.get("bridges", []):
            src, tgt = b["from"], b["to"]
            s_steps = by_stratum.get(src, []) if src.startswith("L") else []
            t_steps = by_stratum.get(tgt, []) if tgt.startswith("L") else []
            flags = []
            # stratum-level endpoints are ambiguous when the stratum holds >1 step
            if len(s_steps) > 1 or len(t_steps) > 1:
                flags.append("underspecified_endpoint")
                defects["bridge_underspecified"] += 1
            if src.startswith(("amp:", "buf:")) and not resolve_moderator(src, modidx):
                flags.append("unmappable_moderator")
            if not b.get("citation"):
                flags.append("uncited")
                defects["bridge_uncited"] += 1
            # ontology-grounded checks: adjacency/direction from stratumOrder
            s_str = src if src.startswith("L") else None
            t_str = "L8" if tgt.startswith("out:") else (tgt if tgt.startswith("L") else None)
            if s_str and t_str:
                for f in bridge_checks(s_str, t_str, b["relation"], idx):
                    flags.append(f)
                    defects[f"bridge_{f}"] += 1

            def render(ep, steps):
                if steps:
                    names = ", ".join(x["step"] for x in steps)
                    return f"{ep} ({STRATUM_LABEL.get(ep,'?')}: {names})"
                return ep
            t = "T6_OUTCOME_BRIDGE" if tgt.startswith("out:") or tgt == "L8" else "T5_BRIDGE"
            items.append(dict(
                item_id=iid(doc, t, src, b["relation"], tgt), item_type=t, doc_id=doc,
                claim=f"{render(src, s_steps)} {b['relation']} {render(tgt, t_steps)} "
                      f"in this case, as a typical (not asserted) pathway.",
                span=b.get("cue", ""), strata=[src if src.startswith("L") else "MOD",
                                               tgt if tgt.startswith("L") else "L8"],
                terms=[src, tgt], kind="extracted", flags=flags,
                citation=b.get("citation", ""), extractor_plausibility=b.get("plausibility")))

    # step -> stratum consistency across the corpus
    inconsistent = {k: dict(v) for k, v in stratum_of_step.items() if len(v) > 1}
    return items, defects, inconsistent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractions", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--ontology", required=True)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    items, defects, inconsistent = build(a)
    dedup = {}
    for it in items:
        dedup.setdefault(it["item_id"], it)
    items = list(dedup.values())

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "items_real.jsonl"), "w") as fh:
        for it in items:
            fh.write(json.dumps(it) + "\n")
    json.dump({"defects": dict(defects), "step_stratum_inconsistent": inconsistent},
              open(os.path.join(a.out, "extractor_defects.json"), "w"), indent=1)

    c = Counter(i["item_type"] for i in items)
    print(f"items: {len(items)}")
    for k in sorted(c):
        print(f"  {k:<20}{c[k]:>5}")
    print("\nextractor defects (found before any agent sees an item):")
    for k, v in sorted(defects.items()):
        print(f"  {k:<28}{v:>5}")
    print(f"\nsteps assigned to >1 stratum across the corpus: {len(inconsistent)}")
    for k, v in list(inconsistent.items())[:6]:
        print(f"  {k:<45}{v}")


if __name__ == "__main__":
    main()
