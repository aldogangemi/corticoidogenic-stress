#!/usr/bin/env python3
"""Run the bridging competency-question unit tests against a data graph (default: the
pattern exemplar A+). b35-CQ-AXIOM-COVERAGE: every CQ must resolve as expected."""
import sys, yaml
from rdflib import Graph
data = sys.argv[1] if len(sys.argv) > 1 else "ontology/csf-bridging-v34.ttl"
g = Graph().parse(data, format="turtle")
spec = yaml.safe_load(open("ontology/csf-bridging-cqs.yaml"))
pre = spec["prefixes"]
ok = tot = 0
for cq in spec["cqs"]:
    tot += 1
    q = pre + "\n" + cq["query"]
    exp = str(cq["test"]["expected"]).lower()
    try:
        r = g.query(q)
        if cq["test"]["kind"] == "sparql_ask":
            got = bool(r.askAnswer)
            passed = (got == (exp == "true"))
            gs = str(got)
        else:
            rows = list(r)
            passed = (len(rows) > 0) == (exp == "non_empty")
            gs = f"{len(rows)} rows"
    except Exception as e:
        gs = "ERR " + str(e)[:40]
        passed = False
    ok += passed
    print(f"  {cq['id']:6s} {cq['polarity']:8s} expect={exp:9s} got={gs:12s} {'PASS' if passed else 'FAIL'}")
print(f"\nCQ coverage: {ok}/{tot} pass (b35)")
