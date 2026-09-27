#!/usr/bin/env python3
"""
design_panel.py — turn CSFO-Polanyi extractions into typed adjudicable items,
add anchors and red-team negatives, and build the agent x item assignment.

Input : extractions.jsonl  (one JSON object per CSF_Situation produced by the
        POLANYI++/CSFO extraction over the LiveJournal corpus)
Output: out/items.jsonl, out/assignment.json, out/payloads/<agent>.jsonl

Design constraints enforced here:
  * every item is ONE atomic claim of ONE type (no bundles);
  * every item carries the verbatim span it was abduced from;
  * >= MIN_JUDGES agents per item, and every agent pair co-judges >= MIN_PAIR
    items (Dawid-Skene cannot identify confusion matrices without overlap);
  * for bridge items: source-layer agent + target-layer agent + >=1 outsider;
  * provenance is stripped (no frame/pipeline confidence, no source model);
  * anchors and red-team items are interleaved and indistinguishable in form.
"""
from __future__ import annotations
import argparse, hashlib, itertools, json, math, os, random
from collections import defaultdict

MIN_JUDGES = 3
MIN_PAIR = 25          # minimum co-judged items per agent pair
ANCHOR_FRACTION = 0.12  # target share of anchor items in the pool
REDTEAM_FRACTION = 0.15

AGENT_STRATA = {
    "A1_SOCIAL": {"L1"},
    "A2_PSYCH": {"L2", "L3"},
    "A3_ENDOCRINE": {"L4", "L5"},
    "A4_NEUROIMMUNE": {"L6"},
    "A5_MOLECULAR": {"L7"},
}
PHENOTYPE_FACING = ["A1_SOCIAL", "A2_PSYCH", "A4_NEUROIMMUNE"]   # judge L9 outcomes

# layer ordering used only to detect direction/timescale defects in red-teaming
LAYER_ORDER = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
# characteristic timescale in seconds, for the non-composability check
TIMESCALE = {"L1": 3e7, "L2": 1e3, "L3": 1e5, "L4": 1e0,
             "L5": 1e3, "L6": 1e6, "L7": 1e7, "L8": 3e7}


def iid(*parts) -> str:
    return "I" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


# --------------------------------------------------------------------------- items
def make_items(extractions, projection):
    """Explode each extracted situation into atomic typed items."""
    term_layer = {}
    for stratum, terms in projection["strata"].items():
        for t in terms:
            term_layer[t["id"]] = stratum

    items = []
    for ex in extractions:
        doc = ex["doc_id"]
        # T1 frame selection
        for fr in ex.get("frames", []):
            items.append(dict(
                item_id=iid(doc, "T1", fr["frame"]), item_type="T1_FRAME", doc_id=doc,
                claim=f"This account instantiates the frame {fr['frame']}.",
                terms=[fr["frame"]], span=fr["span"], layers=["SCHEMA"], kind="extracted"))
        # T2 role fillers
        for r in ex.get("roles", []):
            items.append(dict(
                item_id=iid(doc, "T2", r["role"], r["filler"]), item_type="T2_ROLE", doc_id=doc,
                claim=f"The role {r['role']} is filled by: {r['filler']}.",
                terms=[r["role"]], span=r["span"],
                layers=[term_layer.get(r["role"], "SCHEMA")],
                kind="extracted", literal=r.get("literal", False)))
        # T3 moderators
        for m in ex.get("moderators", []):
            items.append(dict(
                item_id=iid(doc, "T3", m["moderator"]), item_type="T3_MODERATOR", doc_id=doc,
                claim=f"The moderator {m['moderator']} is present in this account.",
                terms=[m["moderator"]], span=m["span"],
                layers=[term_layer.get(m["moderator"], "SCHEMA")],
                kind="extracted", literal=m.get("literal", False)))
        # T4 abduced cascade steps
        for s in ex.get("steps", []):
            items.append(dict(
                item_id=iid(doc, "T4", s["step"]), item_type="T4_STEP", doc_id=doc,
                claim=f"The cascade step {s['step']} is plausibly instantiated in this case.",
                terms=[s["step"]], span=s["span"],
                layers=[term_layer.get(s["step"], "UNKNOWN")], kind="extracted"))
        # T5/T6 bridges
        for b in ex.get("bridges", []):
            ls = term_layer.get(b["from"], "UNKNOWN")
            lt = term_layer.get(b["to"], "UNKNOWN")
            t = "T6_OUTCOME_BRIDGE" if lt == "L9_OUTCOME" else "T5_BRIDGE"
            items.append(dict(
                item_id=iid(doc, t, b["from"], b["relation"], b["to"]), item_type=t, doc_id=doc,
                claim=f"{b['from']} {b['relation']} {b['to']}, as a typical (not asserted) pathway.",
                terms=[b["from"], b["to"]], span=b.get("span", ""), layers=[ls, lt],
                licensing_cue=b.get("licensing_cue", ""), kind="extracted"))
    return items, term_layer


def make_anchors(extractions, term_layer):
    """Anchors: items whose verdict is fixed by the TEXT, not by the literature.

    Positive: a role/moderator whose filler is a verbatim span in the post
              (extraction marked literal=True and verified by string containment).
    Negative: the same slot with a filler drawn from a post that contradicts it.

    Anchors therefore exist only in T2/T3 -- by construction there are none for
    T5/T6, which is why the DS fit must shrink bridge-stratum confusion matrices
    toward the anchored strata rather than estimating them free.
    """
    anchors = {}
    pos, neg = [], []
    for ex in extractions:
        text = ex.get("text", "")
        for key, tname in (("roles", "role"), ("moderators", "moderator")):
            for r in ex.get(key, []):
                if r.get("literal") and r.get("span") and r["span"] in text:
                    it = iid(ex["doc_id"], "T2" if key == "roles" else "T3",
                             r[tname], r.get("filler", ""))
                    pos.append(it)
                    anchors[it] = "SUPPORTED"
    for ex in extractions:
        for r in ex.get("negated", []):     # extractor-flagged explicit denials
            it = iid(ex["doc_id"], "T3", r["moderator"])
            neg.append(it)
            anchors[it] = "CONTRADICTED"
    return anchors, len(pos), len(neg)


def make_anchors_real(items, corpus_dir, rng):
    """Anchors for the real pipeline, from three sources that need no new labelling.

    A) TEXT-GOLD POSITIVES: an L1/L3 step whose extractor cue has >=80% of its
       content words present in the source post. Attestation is a property of the
       text, so this is gold independent of any literature. (Verbatim containment
       is 0/40 on this corpus -- the extractor paraphrases its cues -- so the
       content-word criterion is what makes this source usable at all.)
    B) WELL-FORMEDNESS NEGATIVES: items the ontology itself convicts --
       `Observed` status below L3 (text cannot attest physiology), invalid
       CURIEs, and bridges with no pathwayCitation, which R-B4 requires.
    C) RED-TEAM CALIBRATION HALF: half the injected defective items, held out
       from the specificity estimate so anchoring and scoring do not double-dip.
    """
    import os, re
    anchors, prov = {}, {}
    for it in items:
        if it["kind"] == "redteam":
            continue
        if it["item_type"] == "T4_STEP" and it.get("span") and it["strata"][0] in ("L1", "L3"):
            tp = os.path.join(corpus_dir, it["doc_id"] + ".txt")
            if os.path.exists(tp):
                text = open(tp).read().lower()
                w = re.findall(r"[a-z]{4,}", it["span"].lower())
                if w and sum(x in text for x in w) / len(w) >= 0.8:
                    anchors[it["item_id"]] = "SUPPORTED"; prov[it["item_id"]] = "A_text_gold"
        f = set(it.get("flags") or [])
        if ("observed_below_L3" in f or "unmappable_moderator" in f
                or any(x.startswith("stratum_conflict") for x in f)
                or {"direction_inverted", "layer_skip", "feedsforward_upstream",
                    "feedsback_downstream", "not_cross_stratum"} & f):
            anchors[it["item_id"]] = "CONTRADICTED"; prov[it["item_id"]] = "B_illformed"
    rt = [i["item_id"] for i in items if i["kind"] == "redteam"]
    rng.shuffle(rt)
    for iid in rt[: len(rt) // 2]:
        anchors[iid] = "CONTRADICTED"; prov[iid] = "C_redteam_calib"
    return anchors, prov


def make_redteam(items, term_layer, rng, n):
    """Deliberately defective items, in the same surface form as real ones.

    D1 direction inversion   : a downstream->upstream bridge typed DirectlyPrecedes
    D2 timescale violation   : relata >=4 orders of magnitude apart, no operator
    D3 layer-skip            : bridge across >=3 strata typed DirectlyPrecedes
    D4 frame misattribution  : a frame whose core roles are absent from the doc
    Each carries the defect label for scoring; the label is stripped from payloads.
    """
    bridges = [i for i in items if i["item_type"].startswith("T5")]
    out = []
    for i in range(n):
        if not bridges:
            break
        b = dict(rng.choice(bridges))
        mode = rng.choice(["D1_DIRECTION", "D2_TIMESCALE", "D3_LAYERSKIP"])
        f, t = b["terms"]
        lf, lt = b["strata"]
        if mode == "D1_DIRECTION":
            f, t, lf, lt = t, f, lt, lf
        elif mode == "D2_TIMESCALE":
            base = math.log10(TIMESCALE.get(lf, 1e3))
            lt = max(TIMESCALE, key=lambda k: abs(math.log10(TIMESCALE[k]) - base))
        elif mode == "D3_LAYERSKIP":
            if lf in LAYER_ORDER:
                j = LAYER_ORDER.index(lf)
                lt = LAYER_ORDER[min(j + 3, len(LAYER_ORDER) - 1)]
        b.update(item_id=iid("RT", i, f, t), terms=[f, t], strata=[lf, lt],
                 claim=f"{f} directly precedes {t}, as a typical (not asserted) pathway.",
                 kind="redteam", defect=mode)
        out.append(b)
    return out


# ----------------------------------------------------------------- assignment
def assign(items, rng):
    """Incomplete block design: in-stratum agents first, then outsiders to reach
    MIN_JUDGES and to top up pair overlap."""
    agents = list(AGENT_STRATA)
    assignment = defaultdict(list)          # item_id -> [agents]
    pair_count = defaultdict(int)

    def owners(item):
        o = []
        for a, strata in AGENT_STRATA.items():
            if set(item["strata"]) & strata:
                o.append(a)
        if "L8" in item["strata"]:
            o = list(dict.fromkeys(o + PHENOTYPE_FACING))
        return o

    for it in items:
        chosen = owners(it)
        outsiders = [a for a in agents if a not in chosen]
        rng.shuffle(outsiders)
        # bridges always get >=1 outsider even if both endpoints are owned
        need = max(MIN_JUDGES - len(chosen), 1 if it["item_type"].startswith("T5") or
                   it["item_type"].startswith("T6") else 0)
        chosen += outsiders[:max(0, need)]
        if len(chosen) < MIN_JUDGES:
            chosen += [a for a in outsiders if a not in chosen][:MIN_JUDGES - len(chosen)]
        assignment[it["item_id"]] = sorted(set(chosen))
        for p in itertools.combinations(sorted(set(chosen)), 2):
            pair_count[p] += 1

    # top up thin pairs by adding a third judge to items they already share
    for p, c in sorted(pair_count.items(), key=lambda kv: kv[1]):
        if c >= MIN_PAIR:
            continue
        for it in items:
            if c >= MIN_PAIR:
                break
            cur = assignment[it["item_id"]]
            missing = [a for a in p if a not in cur]
            if len(missing) == 1 and len(cur) < len(AGENT_STRATA):
                cur.append(missing[0])
                assignment[it["item_id"]] = sorted(cur)
                c += 1
        pair_count[p] = c
    return assignment, pair_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractions", required=True)
    ap.add_argument("--projection", default="out/csfo_projection.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--corpus", default="../lj/ljcorpus")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    projection = json.load(open(a.projection))
    extractions = ([] if a.extractions.endswith("items_real.jsonl")
                   else [json.loads(l) for l in open(a.extractions) if l.strip()])

    if a.extractions.endswith("items_real.jsonl"):
        items = [json.loads(l) for l in open(a.extractions) if l.strip()]
        term_layer = {}
        extractions = []
    else:
        items, term_layer = make_items(extractions, projection)
    anchors, npos, nneg = ({}, 0, 0) if not extractions else make_anchors(extractions, term_layer)
    n_rt = int(REDTEAM_FRACTION * len(items))
    items += make_redteam(items, term_layer, rng, n_rt)
    # collapse duplicate claims: the same (doc, type, terms) tuple is ONE item,
    # otherwise DS would treat repeated judgments of one claim as independent.
    dedup, dropped = {}, 0
    for it in items:
        if it["item_id"] in dedup:
            dropped += 1
        else:
            dedup[it["item_id"]] = it
    items = list(dedup.values())
    rng.shuffle(items)

    if not extractions:
        anchors, prov = make_anchors_real(items, a.corpus, rng)
        npos = sum(1 for v in anchors.values() if v == "SUPPORTED")
        nneg = len(anchors) - npos
        json.dump(prov, open(os.path.join(a.out, "anchor_provenance.json"), "w"), indent=1)
    assignment, pair_count = assign(items, rng)

    os.makedirs(os.path.join(a.out, "payloads"), exist_ok=True)
    with open(os.path.join(a.out, "items.jsonl"), "w") as fh:
        for it in items:
            fh.write(json.dumps(it) + "\n")
    json.dump({"assignment": assignment, "anchors": anchors,
               "pair_overlap": {f"{p[0]}|{p[1]}": c for p, c in pair_count.items()}},
              open(os.path.join(a.out, "assignment.json"), "w"), indent=1)

    # per-agent payloads, provenance and defect labels stripped
    PUBLIC = ("item_id", "item_type", "claim", "span", "doc_id")
    for ag in AGENT_STRATA:
        with open(os.path.join(a.out, "payloads", f"{ag}.jsonl"), "w") as fh:
            for it in items:
                if ag in assignment[it["item_id"]]:
                    fh.write(json.dumps({k: it.get(k, "") for k in PUBLIC}) + "\n")

    print(f"items            : {len(items)} unique  (red-team {n_rt}, duplicates collapsed {dropped})")
    print(f"anchors          : {len(anchors)}  (+{npos} attested, -{nneg} denied)"
          f"   [{100*len(anchors)/max(1,len(items)):.1f}% of pool]")
    print(f"judges per item  : min {min(len(v) for v in assignment.values())}"
          f"  mean {sum(len(v) for v in assignment.values())/len(assignment):.2f}")
    print("pair overlap     :")
    for p, c in sorted(pair_count.items()):
        flag = "" if c >= MIN_PAIR else "  << THIN, DS unidentifiable"
        print(f"   {p[0]:<14} {p[1]:<14} {c:>5}{flag}")
    for ag in AGENT_STRATA:
        n = sum(1 for v in assignment.values() if ag in v)
        print(f"  payload {ag:<15} {n:>5} items")


if __name__ == "__main__":
    main()
