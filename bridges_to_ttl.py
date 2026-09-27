#!/usr/bin/env python3
"""bridges_to_ttl.py -- convert a lightweight extraction's `bridges` (+ multilayer,
moderators, outcome) into CrossLayerBridge pattern instances (brg:) so the extracted
cross-layer entrenchment can be SHACL-validated against csf-bridging-shapes.ttl.

Usage: python3 bridges_to_ttl.py lightweight_out_opus/D1.json > D1_bridges.ttl
       python3 bridges_to_ttl.py --validate lightweight_out_opus/D1.json
"""
import json, sys, re, argparse
REL={"directlyPrecedes":"DirectlyPrecedes","feedsForward":"FeedsForward","feedsBack":"FeedsBack",
     "leadsToOutcome":"LeadsToOutcome","amplifies":"Amplifies","buffers":"Buffers"}
LAY={"L1":"L1_Situational","L2":"L2_Appraisal","L3":"L3_Behavioral","L4":"L4_Autonomic",
     "L5":"L5_Endocrine","L6":"L6_InflammatoryMetabolic","L7":"L7_Molecular","L8":"L8_Outcome"}
HDR="""@prefix csf: <http://purl.org/csf/> .
@prefix csc: <http://purl.org/csf/cascades#> .
@prefix mod: <http://purl.org/csf/moderators#> .
@prefix out: <http://purl.org/csf/outcomes#> .
@prefix brg: <http://purl.org/csf/bridging#> .
@prefix pol: <https://w3id.org/polanyi/core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""
def esc(s): return str(s).replace('\\','\\\\').replace('"','\\"')
def to_ttl(js):
    pid=js.get("_pid","case"); B=f"brg:{pid}_"
    out=[HDR, f"{B}situation a csf:CSF_Situation ; rdfs:label \"{pid}\" ;"]
    steps={}          # layer -> node
    for st in (js.get("multilayer") or []):
        lay=st.get("layer","")
        if lay in ("L2","L3","L4","L5","L6","L7","L8"):
            node=f"{B}step_{lay}"; steps[lay]=node
    outs={}           # out:<name> -> node
    lines=[]
    if steps: out.append("    csf:hasCascadeStep "+" , ".join(steps.values())+" ;")
    out[-1]=out[-1].rstrip(" ;")+" ."
    for st in (js.get("multilayer") or []):
        lay=st.get("layer","")
        if lay in steps:
            stat="pol:Abduced" if st.get("status")=="Abduced" else "pol:Observed"
            strat=f' ; csf:atStratum csf:{LAY[lay]}' if lay in LAY else ''
            lines.append(f'{steps[lay]} a csc:CascadeStepOccurrence ; rdfs:label "{lay} {esc(st.get("step",""))} ({st.get("status","")})" ; pol:evidenceStatus {stat}{strat} .')
    # moderator nodes keyed by amp:/buf:<factor>
    modnode={}
    for i,m in enumerate(js.get("moderators") or []):
        role=m.get("role"); fac=m.get("factor","")
        n=f"{B}mod_{i}"; modnode[f"{role[:3]}:{fac}".lower()]=n
        lines.append(f'{n} a mod:{"Amplifier" if role=="amplifier" else "Buffer"} ; rdfs:label "{esc(fac)}" .')
    def resolve(ref):
        ref=(ref or "").strip()
        if ref in steps: return steps[ref]
        if ref.lower().startswith("out:"):
            nm=re.sub(r'[^A-Za-z]','',ref[4:]).capitalize() or "Outcome"
            n=f"{B}out_{nm}"; outs[nm]=n; return n
        key=ref.lower()
        for k,v in modnode.items():
            if key in k or k.split(":")[-1] in key: return v
        return None
    for nm,n in outs.items(): pass
    bridges=js.get("bridges") or []
    for i,b in enumerate(bridges):
        frm=resolve(b.get("from")); to=resolve(b.get("to")); rel=REL.get(b.get("relation"))
        if not(frm and to and rel): continue
        cite=b.get("citation") or ""
        bn=f"{B}bridge_{i}"
        trip=[f'{bn} a brg:CrossLayerBridge',
              f'brg:bridgeFrom {frm}', f'brg:bridgeTo {to}', f'brg:bridgeRelation brg:{rel}',
              f'brg:hasModalStatus brg:Possibile', 'pol:evidenceStatus pol:Abduced',
              f'brg:bridgePlausibility {float(b.get("plausibility",0.5))}'.rstrip("0").rstrip("."),
              f'csf:licensingCue "{esc(b.get("cue",""))}"']
        if cite: trip.append(f'brg:pathwayCitation "{esc(cite)}"')
        lines.append(" ; ".join(trip)+" .")
        out.append(f"{B}situation brg:hasCrossLayerBridge {bn} .")
    # emit outcome individuals (explicitly at the outcome stratum)
    for nm,n in outs.items(): lines.append(f"{n} a out:{nm} ; csf:atStratum csf:L8_Outcome .")
    return "\n".join(out+lines)+"\n"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("json"); ap.add_argument("--validate",action="store_true")
    a=ap.parse_args()
    ttl=to_ttl(json.load(open(a.json)))
    if not a.validate: print(ttl); return
    from rdflib import Graph; from pyshacl import validate
    data=Graph().parse(data=ttl,format="turtle")
    # supply the pattern TBox (relation-type / modal individuals) so sh:class resolves;
    # drop the exemplar ABox so only the extracted case is validated
    patt=Graph().parse("ontology/csf-bridging-v34.ttl",format="turtle")
    for s,p,o in patt:
        if "ex_" not in str(s): data.add((s,p,o))
    data.parse("ontology/csf-strata-v34.ttl",format="turtle")   # Stratum individuals + order
    shapes=Graph().parse("ontology/csf-bridging-shapes.ttl",format="turtle")
    conf,_,txt=validate(data,shacl_graph=shapes,inference="none")
    nb=ttl.count("a brg:CrossLayerBridge")
    print(f"{a.json}: {nb} bridges | SHACL conforms={conf}")
    if not conf: print("\n".join(l for l in txt.splitlines() if "message" in l.lower() or "Result" in l)[:1200])

if __name__=="__main__": main()
