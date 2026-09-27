#!/usr/bin/env python3
"""
strata.py — derive stratum membership FROM THE ONTOLOGY, by query.

With csf-strata.ttl in place, `which stratum is X at?` is answerable:

    Step class  --rdfs:subClassOf--> [onProperty csf:atStratum; hasValue csf:Ln]
    Step type   --rdfs:subClassOf--> Step class          (csf-strata-lift.ttl)
    Occurrence  --rdf:type-------->  Step type

so the stratum of any step type follows from two subclass hops and a hasValue
restriction. This module walks that chain in plain rdflib (no reasoner needed);
`materialise()` yields the same triples an OWL-RL run would add, if you prefer to
feed a materialised dump instead.

Moderators are deliberately NOT stratum-assigned in the ontology: they modulate
steps rather than occupying a level. Their stratum here is a PANEL-SIDE routing
decision (which specialist should judge them), tagged as such, never written back.
"""
from __future__ import annotations
import glob, os
from collections import defaultdict
from rdflib import Graph, RDF, RDFS, OWL, URIRef, Literal, BNode

CSF = "http://purl.org/csf/"
AT_STRATUM = URIRef(CSF + "atStratum")
STRATUM_ORDER = URIRef(CSF + "stratumOrder")
NEXT_STRATUM = URIRef(CSF + "nextStratum")
STRATUM_CLASS = URIRef(CSF + "Stratum")

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
@prefix cpas: <http://www.ontologydesignpatterns.org/schemas/cpannotationschema.owl#> .
@prefix pol: <https://w3id.org/polanyi/core#> .
"""

# panel-side routing only; not ontology content
MODERATOR_ROUTING = {"social": "L1", "contextual": "L1", "developmental": "L1",
                     "cognitive": "L2", "behavioral": "L3", "genetic": "L7"}


def load(ont_dir: str) -> Graph:
    g = Graph()
    for f in sorted(glob.glob(os.path.join(ont_dir, "*.ttl"))):
        g.parse(data=PREFIX_BLOCK + open(f).read(), format="turtle")
    return g


def strata_index(g: Graph) -> dict:
    """The ordered stack: {short: {uri, order, label, next}}, short = 'L1'..'L8'."""
    out = {}
    for s in g.subjects(RDF.type, STRATUM_CLASS):
        short = str(s).split("/")[-1].split("_")[0]
        order = g.value(s, STRATUM_ORDER)
        nxt = g.value(s, NEXT_STRATUM)
        out[short] = dict(uri=str(s), label=str(g.value(s, RDFS.label) or short),
                          order=int(order) if order is not None else None,
                          next=str(nxt).split("/")[-1].split("_")[0] if nxt else None)
    return dict(sorted(out.items(), key=lambda kv: kv[1]["order"] or 99))


def _direct_hasvalue(g, cls):
    """csf:atStratum hasValue restriction asserted directly on a class."""
    for r in g.objects(cls, RDFS.subClassOf):
        if isinstance(r, BNode) and g.value(r, OWL.onProperty) == AT_STRATUM:
            v = g.value(r, OWL.hasValue)
            if v is not None:
                return v
    return None


def stratum_of_class(g, cls, _seen=None):
    """Walk superclasses until a hasValue restriction on csf:atStratum is found."""
    _seen = _seen or set()
    if cls in _seen:
        return None
    _seen.add(cls)
    v = _direct_hasvalue(g, cls)
    if v is not None:
        return v
    for sup in g.objects(cls, RDFS.subClassOf):
        if isinstance(sup, URIRef):
            r = stratum_of_class(g, sup, _seen)
            if r is not None:
                return r
    return None


def assign_all(g: Graph) -> dict:
    """{entity_uri: (stratum_short, provenance)} for every entity the chain reaches."""
    idx = strata_index(g)
    by_uri = {v["uri"]: k for k, v in idx.items()}
    out = {}

    # 1. direct assertions: X csf:atStratum csf:Ln
    for s, o in g.subject_objects(AT_STRATUM):
        if str(o) in by_uri and isinstance(s, URIRef):
            out[str(s)] = (by_uri[str(o)], "asserted")

    # 2. class-level hasValue restrictions, inherited down rdfs:subClassOf
    #    (this is where csf-strata-lift.ttl does its work)
    classes = {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
    classes |= {c for c in g.objects(None, RDFS.subClassOf) if isinstance(c, URIRef)}
    classes |= {c for c in g.subjects(RDFS.subClassOf, None) if isinstance(c, URIRef)}
    for c in classes:
        if str(c) in out:
            continue
        v = stratum_of_class(g, c)
        if v is not None and str(v) in by_uri:
            out[str(c)] = (by_uri[str(v)], "class-restriction")

    # 3. individuals inherit from their types
    for s, t in g.subject_objects(RDF.type):
        if not isinstance(s, URIRef) or not isinstance(t, URIRef):
            continue
        if str(s) in out:
            continue
        if str(t) in out:
            out[str(s)] = (out[str(t)][0], "type-inherited")
    return out, idx


def materialise(g: Graph):
    """Triples an OWL-RL run would add: <entity> csf:atStratum <csf:Ln>."""
    assigned, idx = assign_all(g)
    uri = {k: v["uri"] for k, v in idx.items()}
    return [(URIRef(e), AT_STRATUM, URIRef(uri[s])) for e, (s, _) in assigned.items()]


# ---------------------------------------------------------------- bridge checks
def bridge_checks(src_stratum, tgt_stratum, relation, idx):
    """Ontology-grounded well-formedness checks, now that strata are ordered.

    Replaces the hand-tuned heuristics: adjacency, direction and layer-skip are
    read off csf:stratumOrder / csf:nextStratum rather than guessed.
    """
    flags = []
    if src_stratum not in idx or tgt_stratum not in idx:
        return ["unresolved_endpoint"]
    a, b = idx[src_stratum]["order"], idx[tgt_stratum]["order"]
    rel = (relation or "").lower()
    if a == b:
        flags.append("not_cross_stratum")            # bridging pattern misapplied
    if rel in ("directlyprecedes",):
        if b < a:
            flags.append("direction_inverted")       # downstream -> upstream
        elif b - a >= 2:
            flags.append("layer_skip")               # not nextStratum-adjacent
    if rel == "feedsback" and b > a:
        flags.append("feedsback_downstream")
    if rel == "feedsforward" and b < a:
        flags.append("feedsforward_upstream")
    if rel == "leadstooutcome" and tgt_stratum != "L8":
        flags.append("outcome_relation_offtarget")
    return flags


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology", default="../csfo3/ontology")
    ap.add_argument("--dump", help="write materialised atStratum triples as N-Triples")
    a = ap.parse_args()
    g = load(a.ontology)
    assigned, idx = assign_all(g)
    print(f"triples: {len(g)}")
    print("\nstratum stack (from csf:stratumOrder / csf:nextStratum):")
    for k, v in idx.items():
        print(f"  {k}  order {v['order']}  {v['label']:<28} next={v['next']}")
    cnt, prov = defaultdict(int), defaultdict(int)
    for e, (s, p) in assigned.items():
        cnt[s] += 1
        prov[p] += 1
    print("\nentities assigned per stratum:")
    for k in idx:
        print(f"  {k}: {cnt.get(k,0)}")
    print("provenance:", dict(prov), " total", len(assigned))
    if a.dump:
        with open(a.dump, "w") as fh:
            for s, p, o in materialise(g):
                fh.write(f"<{s}> <{p}> <{o}> .\n")
        print("materialised ->", a.dump)
