#!/usr/bin/env python3
"""
build_panel.py — derive per-agent adjudication contexts from the CSFO network.

Reads csfo/ontology/*.ttl, partitions the signature into layer strata, and emits
one system context per agent at three projection depths (L0/L1/L2), plus the
item and verdict schemas and the assignment design.

Nothing here is hand-written ontology content: every term shown to an agent is
pulled from the TTL, so the contexts stay in sync with the ontology version.

Usage:
    python3 build_panel.py --ontology ../csfo/ontology --out out
"""
from __future__ import annotations
import argparse, glob, json, os, re, textwrap
from collections import defaultdict
from rdflib import Graph, RDF, RDFS, OWL, URIRef, Literal
from strata import load as load_graph, assign_all, strata_index, MODERATOR_ROUTING

# Some CSFO modules use prefixes before declaring them (strict Turtle violation);
# we inject a canonical prefix block rather than editing the source files.
PREFIX_BLOCK = """@prefix csf: <http://purl.org/csf/> .
@prefix csc: <http://purl.org/csf/cascades#> .
@prefix frm: <http://purl.org/csf/frames#> .
@prefix mod: <http://purl.org/csf/moderators#> .
@prefix out: <http://purl.org/csf/outcomes#> .
@prefix sit: <http://purl.org/csf/situations#> .
@prefix int: <http://purl.org/csf/interventions#> .
@prefix brg: <http://purl.org/csf/bridging#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix dul: <http://www.ontologydesignpatterns.org/ont/dul/DUL.owl#> .
@prefix pol: <https://w3id.org/polanyi/core#> .
"""

NS = {
    "csf": "http://purl.org/csf/",
    "csc": "http://purl.org/csf/cascades#",
    "frm": "http://purl.org/csf/frames#",
    "mod": "http://purl.org/csf/moderators#",
    "out": "http://purl.org/csf/outcomes#",
    "sit": "http://purl.org/csf/situations#",
    "brg": "http://purl.org/csf/bridging#",
}

# Strata are no longer declared here. They are derived from csf-strata.ttl by
# query (see strata.py): class-level hasValue restrictions on csf:atStratum,
# inherited down rdfs:subClassOf through csf-strata-lift.ttl, then to individuals
# via rdf:type. Moderators carry no atStratum in the ontology -- by design, they
# modulate rather than occupy a level -- so they are routed to a specialist by
# their declared subtype, which is a panel-side decision (MODERATOR_ROUTING).
MODERATOR_CLASS_ROUTING = {
    "SocialModerator": "L1", "ContextualModerator": "L1", "DevelopmentalModerator": "L1",
    "CognitiveModerator": "L2", "BehavioralModerator": "L3", "GeneticModerator": "L7",
}

# ---------------------------------------------------------------------------
# AGENTS. Each owns 1-2 strata. Rationale for the groupings is in README.md;
# the load-bearing one: L5 and L6 are deliberately NOT co-owned, so the
# contested glucocorticoid-resistance -> unopposed-inflammation bridge stays
# cross-agent and therefore visible to the panel.
# ---------------------------------------------------------------------------
AGENTS = {
    "A1_SOCIAL":      dict(owns=["L1"], persona="social epidemiology and social genomics"),
    "A2_PSYCH":       dict(owns=["L2", "L3"], persona="cognitive-affective science (appraisal) and health behaviour"),
    "A3_ENDOCRINE":   dict(owns=["L4", "L5"], persona="psychoneuroendocrinology and autonomic psychophysiology"),
    "A4_NEUROIMMUNE": dict(owns=["L6"], persona="psychoneuroimmunology and inflammatory/metabolic stress biology"),
    "A5_MOLECULAR":   dict(owns=["L7"], persona="behavioural genetics, epigenetics and stress neurobiology"),
}
SHARED_STRATUM = "L8"   # in every context as target vocabulary

ITEM_TYPES = ["T1_FRAME", "T2_ROLE", "T3_MODERATOR", "T4_STEP", "T5_BRIDGE", "T6_OUTCOME_BRIDGE"]


# ---------------------------------------------------------------------------
def load(ont_dir: str) -> Graph:
    g = Graph()
    for f in sorted(glob.glob(os.path.join(ont_dir, "*.ttl"))):
        g.parse(data=PREFIX_BLOCK + open(f).read(), format="turtle")
    return g


def q(pfx, local):
    return URIRef(NS[pfx] + local)


def curie(u) -> str:
    for p, n in sorted(NS.items(), key=lambda kv: -len(kv[1])):
        if str(u).startswith(n):
            return f"{p}:{str(u)[len(n):]}"
    return str(u)


def lit(g, s, p):
    for o in g.objects(s, p):
        if isinstance(o, Literal):
            return " ".join(str(o).split())
    return None


def describe(g, s) -> dict:
    d = {"id": curie(s), "label": lit(g, s, RDFS.label) or curie(s).split(":")[-1]}
    c = lit(g, s, RDFS.comment)
    if c:
        d["comment"] = c[:400]
    pm = lit(g, s, q("csf", "pubmedID"))
    if pm:
        d["pmid"] = pm
    ev = lit(g, s, q("csf", "evidenceStrength"))
    if ev:
        d["evidence"] = ev
    return d


def collect(g) -> dict:
    """Partition the signature by ontology-asserted stratum."""
    assigned, idx = assign_all(g)
    strata = {k: [] for k in idx}
    seen = set()
    for uri, (short, prov) in sorted(assigned.items()):
        s = URIRef(uri)
        if s in seen or uri.rsplit("#", 1)[-1].startswith("ex_"):
            continue   # skip the bridging module's worked-example individuals
        seen.add(s)
        d = describe(g, s)
        d["stratum_provenance"] = prov
        strata[short].append(d)

    # moderators: no atStratum by design -> panel-side routing by subclass
    for cls, short in MODERATOR_CLASS_ROUTING.items():
        c = q("mod", cls)
        for s in list(g.subjects(RDF.type, c)) + list(g.subjects(RDFS.subClassOf, c)):
            if s in seen or not isinstance(s, URIRef):
                continue
            for x in ([s] + list(g.subjects(RDF.type, s))):
                if x in seen or not isinstance(x, URIRef):
                    continue
                seen.add(x)
                d = describe(g, x)
                d["stratum_provenance"] = "panel-routing (moderator)"
                strata[short].append(d)

    frames = []
    for f in g.subjects(RDF.type, q("csf", "CorticoidogenicFrame")):
        d = describe(g, f)
        d["roles"] = sorted(curie(r) for r in g.objects(f, q("csf", "hasCSFRole")))
        frames.append(d)
    for f in g.subjects(RDF.type, q("frm", "ProtectiveFrame")):
        d = describe(g, f)
        d["protective"] = True
        frames.append(d)

    bridge_rel = [describe(g, s) for s in g.subjects(RDF.type, q("brg", "BridgeRelationType"))]
    return {"strata": strata, "index": idx,
            "frames": sorted(frames, key=lambda d: d["id"]),
            "bridge_relations": bridge_rel}


# ---------------------------------------------------------------------------
# Context rendering
# ---------------------------------------------------------------------------
def fmt_terms(items, with_comment=True, limit=None):
    out = []
    for d in (items[:limit] if limit else items):
        line = f"- `{d['id']}` — {d['label']}"
        if with_comment and d.get("comment"):
            line += f". {d['comment']}"
        extra = []
        if d.get("pmid"):
            extra.append(f"PMID {d['pmid']}")
        if d.get("evidence"):
            extra.append(d["evidence"])
        if extra:
            line += f" [{'; '.join(extra)}]"
        out.append(line)
    return "\n".join(out) if out else "- (none)"


JUDGMENT_BLOCK = """
## What you must return

For every item you receive, return one JSON object. No prose outside the JSON.

```json
{
  "item_id": "<echo>",
  "wellformed": "WELLFORMED | REFERENT_MISMATCH | TIMESCALE_NONCOMPOSABLE | INDIVIDUATION_CLASH",
  "verdict": "SUPPORTED | UNDERDETERMINED | CONTRADICTED | OUT_OF_SCOPE",
  "stance": "ENTAILS | COMPATIBLE | INCOMPATIBLE | NA",
  "defeater": "<the single minimal observation that would flip your verdict>",
  "confidence": 0.0
}
```

Field rules — these are strict, and the aggregation breaks if you bend them:

- `wellformed` — answer for EVERY item, including ones outside your competence.
  This is a question about the *construction* of the claim, not its truth.
  `REFERENT_MISMATCH`: the same term denotes different things at different layers.
  `TIMESCALE_NONCOMPOSABLE`: the relata live on timescales that cannot compose
  without an aggregation operator, and none is stated.
  `INDIVIDUATION_CLASH`: the relata are individuated incompatibly (trait vs.
  episode vs. process).
- `verdict` — the evidential status of the claim, judged ONLY from your own
  layer's evidence base. Return `OUT_OF_SCOPE` when the item does not touch your
  layer. `OUT_OF_SCOPE` and `UNDERDETERMINED` are NOT interchangeable:
  `OUT_OF_SCOPE` means "not mine to judge"; `UNDERDETERMINED` means "mine, and
  my layer's evidence does not decide it". Confusing them corrupts the panel.
- `stance` — bridge items only; otherwise `NA`. Does your layer's commitments
  entail the claim, merely tolerate it, or rule it out?
- `defeater` — one sentence, concrete and measurable. Not scored; it becomes the
  resolution condition if the item ends up unresolved.
- `confidence` — your own, in [0,1]. Diagnostic only; it is not used as a weight.

## How to judge

- Judge each item on its own. Do not infer that an item is likely correct
  because it looks like output from a curated pipeline; you are not told where
  items come from, and some are deliberately malformed.
- Do not calibrate toward what you expect other specialists to say. Disagreement
  is the signal being measured; agreement you manufacture destroys it.
- Abstain freely. An honest `OUT_OF_SCOPE` is worth more than a guess.
- The diary text is self-report. Attested means attested *in the text*; it does
  not license claims about the writer's physiology.
"""


def render_context(agent_id, spec, data, depth, corpus_note):
    owns = spec["owns"]
    others = [k for k in data['index'] if k not in owns and k != SHARED_STRATUM]
    own_terms = [t for k in owns for t in data["strata"][k]]
    parts = []

    parts.append(f"""# Adjudicator {agent_id} — {' + '.join(data['index'][k]['label'] for k in owns)}

You are a specialist adjudicator on a multi-layer panel. Your expertise is
{spec['persona']}. Four other specialists, each covering different layers, judge
overlapping subsets of the same items independently. You will not see their
judgments, and they will not see yours.

Your competence is **{', '.join(data['index'][k]['label'] for k in owns)}**. You are
expected to be authoritative there and to abstain elsewhere.

Projection depth: **{depth}**.
""")

    if depth in ("L1", "L2"):
        parts.append(f"""## Your layer vocabulary

These are the only terms you are authoritative over. An item is *yours* if it
mentions at least one of them.

{fmt_terms(own_terms)}
""")
        parts.append(f"""## Shared schema-level vocabulary

Frames are situation-type descriptions that items may invoke. You judge whether a
frame's fit is supported *by your layer's evidence*, not whether the frame is a
good frame.

{fmt_terms(data['frames'], with_comment=False)}

Outcome vocabulary (target of terminal bridges):

{fmt_terms(data['strata'][SHARED_STRATUM], with_comment=False, limit=40)}
""")
        parts.append(f"""## Neighbouring layers — signature only

You are given the *names* of terms in other layers so that you can read an item,
but no axioms about them. Where an item turns on one of these, say so in your
`defeater` rather than judging it.

{chr(10).join('**' + data['index'][k]['label'] + '**: ' + ', '.join('`'+t['id']+'`' for t in data['strata'][k][:25]) for k in others)}
""")
    else:  # L0
        parts.append(f"""## Vocabulary (signature only)

You are given term names and no axioms. Judge from your own domain knowledge.

**Yours**: {', '.join('`'+t['id']+'`' for t in own_terms)}

**Other layers**: {', '.join('`'+t['id']+'`' for k in others for t in data['strata'][k][:15])}
""")

    if depth == "L2":
        rels = "\n".join(f"- `{d['id']}` — {d['label']}" + (f". {d.get('comment','')}" if d.get("comment") else "")
                         for d in data["bridge_relations"])
        parts.append(f"""## Cross-layer bridging axioms (FULL PROJECTION)

A cross-layer bridge is a reified, defeasible claim that one element typically
influences another, carrying a modal status, a plausibility value, a licensing
cue and a literature citation. Bridges are *possibilia*: they assert typical
relevance, not actual causation in this case.

Relation types:
{rels}

Constraints the bridge pattern imposes, which you may use in judging:
- a bridge marked `brg:Actual` whose target is only `pol:Abduced` is a defect;
- every bridge must carry a `brg:pathwayCitation`;
- a bridge must carry exactly one typed relation;
- moderator bridges must use `Amplifies` or `Buffers` and target a step.
""")

    parts.append(corpus_note)
    parts.append(JUDGMENT_BLOCK)
    return "\n".join(parts)


CORPUS_NOTE = """
## The material

Items are derived from personal diary posts (public LiveJournal entries) in which
the writer describes stressful circumstances in their own words. Each item quotes
the span it was derived from. The writers are not patients, no physiological
measurement exists for any of them, and no diagnosis has been made. Anything
below the behavioural layer is necessarily abduced from text, never observed.

Treat that asymmetry as central: for the social, cognitive and behavioural
layers, the text can *attest*; for the autonomic, endocrine, immune, neural and
molecular layers, the text can at most *license an abduction*. An item claiming
attestation at those layers is defective regardless of how plausible its content
is.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology", default="../csfo/ontology")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    g = load_graph(a.ontology)
    data = collect(g)

    os.makedirs(os.path.join(a.out, "contexts"), exist_ok=True)
    manifest = {"agents": {}, "item_types": ITEM_TYPES,
                "strata": {k: {"label": v["label"], "order": v["order"],
                               "next": v["next"], "n_terms": len(data["strata"][k])}
                           for k, v in data["index"].items()}}
    for aid, spec in AGENTS.items():
        manifest["agents"][aid] = {"owns": spec["owns"], "persona": spec["persona"], "contexts": {}}
        for depth in ("L0", "L1", "L2"):
            txt = render_context(aid, spec, data, depth, CORPUS_NOTE)
            p = os.path.join(a.out, "contexts", f"{aid}.{depth}.md")
            open(p, "w").write(txt)
            manifest["agents"][aid]["contexts"][depth] = os.path.relpath(p, a.out)

    json.dump(data, open(os.path.join(a.out, "csfo_projection.json"), "w"), indent=1)
    json.dump(manifest, open(os.path.join(a.out, "panel_manifest.json"), "w"), indent=1)

    print(f"triples parsed      : {len(g)}")
    for k, v in data["strata"].items():
        print(f"  {k:<16} {len(v):>3} terms   {data['index'][k]['label']}")
    print(f"  frames           {len(data['frames']):>3}")
    print(f"contexts written    : {len(AGENTS)*3} -> {a.out}/contexts/")
    for aid in AGENTS:
        sz = [os.path.getsize(os.path.join(a.out, 'contexts', f'{aid}.{d}.md')) for d in ("L0", "L1", "L2")]
        print(f"  {aid:<15} L0={sz[0]:>6}B  L1={sz[1]:>6}B  L2={sz[2]:>6}B")


if __name__ == "__main__":
    main()
