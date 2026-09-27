#!/usr/bin/env python3
"""
compare_full_vs_lightweight.py -- agreement analysis between the FULL POLANYI++
launcher XKGs (xkg_reports/*.ttl) and the LIGHTWEIGHT extractions
(lightweight_out/*.json), per LJ post. Quantifies what the heavy pipeline adds.

Extraction from the full XKG is by LOCAL NAME across namespaces (robust to
whatever prefixes the launcher writes): frame via a predicate 'evokes' → frm:X;
ISIT via any isit:* datatype property; multilayer via 'evidenceStatus'/'hasLayer';
DARKSIDE via dark:*; DEEPJUDGE via judge:*.

Outputs: comparison.csv, comparison_report.md, fig_compare.png.
Usage: python3 compare_full_vs_lightweight.py --full xkg_reports --light lightweight_out
"""
import argparse, glob, json, os, re, numpy as np
from collections import Counter
from rdflib import Graph, Literal

FRAME_LETTER = {"ChronicSubordinationFrame":"A","UncontrollableThreatFrame":"B",
 "CaregivingEntrapmentFrame":"C","EarlyLifeProgrammingFrame":"D","ChronicIdentityThreatFrame":"E",
 "CircadianMisalignmentFrame":"F","SocialDisconnectionFrame":"G","SocioeconomicStrainFrame":"H",
 "SocialSupportFrame":"P1","AgencyRestorationFrame":"P2","CulturalContinuityFrame":"P3","MeaningMakingFrame":"P4"}
def _ln(u): return re.split(r'[#/]', str(u))[-1]
def frame_letter(u):
    """robust name->letter: handles frm:XFrame AND frm:XFrame_instance_1 (evokes ABox)."""
    n=_ln(u)
    if n in FRAME_LETTER: return FRAME_LETTER[n]
    m=re.match(r'([A-Za-z]+Frame)', n)            # strip _instance_N and similar suffixes
    return FRAME_LETTER.get(m.group(1)) if m else None

def parse_full(path):
    """Calibrated to the real launcher XKG (see D1.ttl): frames come as a
    DEEPJUDGE Warranted SET (judge_frame_* VerificationSheet, verdict Warranted,
    label 'DeepJudge verdict: <FrameName>'); ISIT via isit: CSFO dims; abduced
    physiology via pol:evidenceStatus on cascade_l* steps (deduped by step type);
    DARKSIDE fully reified (dark:)."""
    g=Graph()
    try: g.parse(path,format="turtle")
    except Exception as e: return {"error":str(e)[:60]}
    warranted=set(); evoked=set(); isit={}; abd_types=set(); observed=0
    darkside=None; delegation=None; precision={}; ija=None
    # verdict per judge_frame subject
    verdicts={}
    for s,p,o in g:
        ln=_ln(p)
        if ln=="verdict": verdicts.setdefault(s,_ln(o))
        if ln=="evokes":
            fl=frame_letter(o)
            if fl: evoked.add(fl)
        if "isit" in str(p).lower() and isinstance(o,Literal):
            try: isit[ln]=float(o)
            except: pass
        if ln=="evidenceStatus" and _ln(o)=="Abduced":
            t=[_ln(oo) for pp,oo in g.predicate_objects(s) if _ln(pp)=="type"]
            abd_types.add(t[0] if t else str(s))          # dedupe by step type
        elif ln=="evidenceStatus" and _ln(o)=="Observed": observed+=1
        if ln=="contradictionType" and "dark" in str(p).lower(): darkside=str(o)
        if ln in ("delegationRisk","delegationVerdict"): delegation=str(o)
        if ln in ("explicitPrecision","tacitYield","overProjectionRate","warrantedRate"):
            try: precision[ln]=float(o)
            except: pass
        if ln=="interJudgeAgreement":
            try: ija=float(o)
            except: pass
    # map Warranted VerificationSheets -> frame letters (via their label or evokes)
    VALID=set(FRAME_LETTER.values())
    for subj,v in verdicts.items():
        if v!="Warranted": continue
        for pp,oo in g.predicate_objects(subj):
            if _ln(pp)=="label":
                lab=str(oo); fl=None
                m=re.search(r'([A-Za-z]+Frame)\b', lab)            # "…UncontrollableThreatFrame"
                if m: fl=FRAME_LETTER.get(m.group(1))
                if not fl:                                          # "Frame B" / "Frame P1"
                    m=re.search(r'\bFrame\s+([A-H]|P[1-4])\b', lab)
                    if m and m.group(1) in VALID: fl=m.group(1)
                if not fl:                                          # "(UncontrollableThreat)"
                    m=re.search(r'\(([A-Za-z]+)\)', lab)
                    if m: fl=FRAME_LETTER.get(m.group(1)+"Frame")
                if fl: warranted.add(fl)
    if not warranted:                                     # no reified judge_frame nodes
        # prose DEEPJUDGE verdicts in rdfs:comment: "frm:XFrame = Warranted" / "frm:XFrame: WARRANTED"
        for s,p,o in g:
            if _ln(p)=="comment" and isinstance(o,Literal):
                txt=str(o)
                # tolerate "= Warranted", ": WARRANTED", "(B) — Warranted"; don't cross ;/. item breaks
                for m in re.finditer(r'frm:([A-Za-z]+Frame)\b[^;.\n]{0,25}?\b(Warranted|WARRANTED)\b', txt):
                    fl=FRAME_LETTER.get(m.group(1))
                    if fl: warranted.add(fl)
                # dominance language (D39-style prose with no verdict triples): "dominant XFrame", "XFrame (H) PRIMARY"
                for m in re.finditer(r'(?:dominant|primary|PRIMARY)\s+(?:CSF\s+|corticoidogenic\s+)?(?:frame\s+)?([A-Za-z]+Frame)\b', txt):
                    fl=FRAME_LETTER.get(m.group(1))
                    if fl: warranted.add(fl)
                for m in re.finditer(r'([A-Za-z]+Frame)\s*\([A-H]\)\s*(?:is\s+)?(?:the\s+)?(?:PRIMARY|primary|dominant)', txt):
                    fl=FRAME_LETTER.get(m.group(1))
                    if fl: warranted.add(fl)
    if not warranted: warranted=evoked                    # last resort: raw evokes (may include Weak)
    corticog=[f for f in warranted if len(f)==1]          # A-H (drop protective P#)
    lead=sorted(corticog)[0] if corticog else (sorted(warranted)[0] if warranted else None)
    return {"frame":lead, "frames":warranted, "isit":isit, "abduced":len(abd_types),
            "observed":observed, "darkside":darkside, "delegationRisk":delegation,
            "verdict":"Warranted" if lead in warranted else None,
            "precision":precision, "interJudgeAgreement":ija}

def parse_light(path):
    d=json.load(open(path))
    if "error" in d: return {"error":d["error"]}
    ml=d.get("multilayer",[]) or []
    isit={}; isit.update(d.get("isit_semantic",{}) or {}); isit.update(d.get("isit_csfo",{}) or {})
    prim=(d.get("primaryFrame") or {}).get("id")
    fset={prim} if prim else set()
    fset|= {(f or {}).get("id") for f in (d.get("secondaryFrames") or []) if (f or {}).get("id")}
    return {"frame":prim, "frames":{f for f in fset if f},
            "isit":{k:float(v) for k,v in isit.items() if isinstance(v,(int,float))},
            "abduced":sum(1 for s in ml if s.get("status")=="Abduced"),
            "observed":sum(1 for s in ml if s.get("status")=="Observed"),
            "layers":sorted({s.get("layer") for s in ml if s.get("layer")}),
            "darkside":(d.get("darkside") or {}).get("contradictionType"),
            "delegationRisk":(d.get("darkside") or {}).get("delegationRisk"),
            "verdict":(d.get("frameVerdict") or {}).get("status")}

def spear(a,b):
    a=np.asarray(a,float); b=np.asarray(b,float)
    if len(a)<4: return np.nan
    ra=np.argsort(np.argsort(a)).astype(float); rb=np.argsort(np.argsort(b)).astype(float)
    ra-=ra.mean(); rb-=rb.mean(); d=np.sqrt((ra@ra)*(rb@rb)); return float(ra@rb/d) if d>0 else np.nan

def kappa(a,b):
    labs=sorted(set(a)|set(b)); n=len(a)
    po=sum(x==y for x,y in zip(a,b))/n
    ca=Counter(a); cb=Counter(b); pe=sum((ca[l]/n)*(cb[l]/n) for l in labs)
    return (po-pe)/(1-pe) if pe<1 else 1.0, po

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--full",default="xkg_reports"); ap.add_argument("--light",default="lightweight_out")
    a=ap.parse_args()
    pids=sorted({os.path.basename(f)[:-4] for f in glob.glob(os.path.join(a.full,"*.ttl"))} &
                {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(a.light,"*.json"))},
                key=lambda p:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',p)])
    if not pids: print("No overlapping post IDs between --full and --light. "
                       "Produce both sets first (run_polanyi_csfo.py + run_lightweight_csfo.py)."); return
    rows=[]
    for pid in pids:
        F=parse_full(os.path.join(a.full,pid+".ttl")); L=parse_light(os.path.join(a.light,pid+".json"))
        ff=F.get("frames",set()) or set(); lf=L.get("frames",set()) or set()
        inset = (L.get("frame") in ff) if (L.get("frame") and ff) else None
        jac = (len(ff&lf)/len(ff|lf)) if (ff|lf) else None
        rows.append(dict(pid=pid, full_frame=F.get("frame"), light_frame=L.get("frame"),
            full_frames="|".join(sorted(ff)), light_frames="|".join(sorted(lf)),
            light_in_full_set=inset, frame_jaccard=(round(jac,2) if jac is not None else None),
            full_abd=F.get("abduced"), light_abd=L.get("abduced"),
            full_verdict=F.get("verdict"), light_verdict=L.get("verdict"),
            full_dark=bool(F.get("darkside") and F.get("darkside")!="null"),
            light_dark=bool(L.get("darkside") and L.get("darkside")!="null"),
            _fisit={k:v for k,v in F.get("isit",{}).items() if k!="dimensionScore"},
            _lisit=L.get("isit",{}), _ff=ff, _lf=lf))
    import csv as _csv
    with open("comparison.csv","w",newline="") as fh:
        w=_csv.DictWriter(fh,fieldnames=[k for k in rows[0] if not k.startswith("_")]); w.writeheader()
        for r in rows: w.writerow({k:v for k,v in r.items() if not k.startswith("_")})

    # frame agreement
    fp=[(r["full_frame"],r["light_frame"]) for r in rows if r["full_frame"] and r["light_frame"]]
    R=["# Full vs Lightweight CSFO extraction — agreement\n",
       f"Posts compared: {len(pids)} (both sets present).\n"]
    if fp:
        fa=[x for x,_ in fp]; la=[y for _,y in fp]
        k,po=kappa(fa,la)
        R.append(f"## Frame agreement\n### Leading corticoidogenic frame (full-lead vs light-primary)\n"
                 f"- exact agreement: {po:.0%} ({len(fp)} posts)\n- Cohen's κ: {k:.2f}")
        conf=Counter(fp); dis=[f"{x}→{y}×{n}" for (x,y),n in conf.most_common() if x!=y]
        R.append("- top disagreements (full→light): "+(", ".join(dis[:8]) or "none"))
        # set-based agreement (full is a Warranted co-activation set, not one label)
        mem=[r["light_in_full_set"] for r in rows if r["light_in_full_set"] is not None]
        jac=[r["frame_jaccard"] for r in rows if r["frame_jaccard"] is not None]
        R.append("### Co-activation set (DEEPJUDGE Warranted set vs light primary+secondary)")
        if mem: R.append(f"- light primary ∈ full Warranted set: {sum(mem)/len(mem):.0%} of posts (n={len(mem)})")
        if jac: R.append(f"- mean frame-set Jaccard: {np.mean(jac):.2f} (n={len(jac)})")
    # ISIT correlations on shared dims
    dims=set()
    for r in rows: dims|= set(r["_fisit"])&set(r["_lisit"])
    R.append("\n## ISIT dimensions (Spearman across posts, shared dims only)")
    isit_rows=[]
    for dim in sorted(dims):
        xs=[(r["_fisit"][dim],r["_lisit"][dim]) for r in rows if dim in r["_fisit"] and dim in r["_lisit"]]
        if len(xs)>=4:
            rho=spear([x for x,_ in xs],[y for _,y in xs])
            mad=np.mean([abs(x-y) for x,y in xs])
            R.append(f"- {dim:20s} ρ={rho:+.2f}  mean|Δ|={mad:.2f}  (n={len(xs)})")
            isit_rows.append((dim,rho,mad,len(xs)))
    if not dims: R.append("- (no overlapping ISIT dims — full uses "
        "official valence/arousal/… while lightweight also emits them; check the full XKG's isit: block)")
    # multilayer + verdict + darkside
    fabd=[r["full_abd"] for r in rows if r["full_abd"] is not None]
    labd=[r["light_abd"] for r in rows if r["light_abd"] is not None]
    R.append(f"\n## Multilayer abduction\n- mean abduced steps/post — full {np.mean(fabd):.1f} vs light {np.mean(labd):.1f}"
             if fabd and labd else "\n## Multilayer abduction\n- (insufficient data)")
    vp=[(r["full_verdict"],r["light_verdict"]) for r in rows if r["full_verdict"] and r["light_verdict"]]
    if vp: R.append(f"- DEEPJUDGE verdict agreement: {sum(x==y for x,y in vp)/len(vp):.0%} (n={len(vp)})")
    dp=[(r["full_dark"],r["light_dark"]) for r in rows]
    R.append(f"- DARKSIDE presence agreement: {sum(x==y for x,y in dp)/len(dp):.0%}")
    R.append("\n## Reading\n- High frame κ + ISIT ρ ⇒ the lightweight path is a faithful, cheap proxy "
             "(scale with it, spot-check with the full pipeline). Systematic ISIT offsets or frame "
             "confusions localize *what the full apparatus adds* — the paper's answer to 'what does "
             "the heavy pipeline buy?'.")
    open("comparison_report.md","w").write("\n".join(R))
    print("\n".join(R))

    # figure
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig,ax=plt.subplots(1,2,figsize=(12,4.4))
        if fp:
            labs=sorted(set(fa)|set(la)); idx={l:i for i,l in enumerate(labs)}
            M=np.zeros((len(labs),len(labs)))
            for x,y in fp: M[idx[x],idx[y]]+=1
            im=ax[0].imshow(M,cmap="Blues"); ax[0].set_xticks(range(len(labs))); ax[0].set_xticklabels(labs,fontsize=7)
            ax[0].set_yticks(range(len(labs))); ax[0].set_yticklabels(labs,fontsize=7)
            ax[0].set_xlabel("lightweight"); ax[0].set_ylabel("full"); ax[0].set_title("(A) frame confusion")
            for i in range(len(labs)):
                for j in range(len(labs)):
                    if M[i,j]: ax[0].text(j,i,int(M[i,j]),ha="center",va="center",fontsize=8)
        if isit_rows:
            ax[1].barh(range(len(isit_rows)),[r[1] for r in isit_rows],color="#2980b9")
            ax[1].set_yticks(range(len(isit_rows))); ax[1].set_yticklabels([r[0] for r in isit_rows],fontsize=8)
            ax[1].set_xlim(-1,1); ax[1].axvline(0,color="k",lw=.6); ax[1].set_xlabel("Spearman ρ (full vs light)")
            ax[1].set_title("(B) ISIT-dim agreement")
        fig.suptitle("Full POLANYI++ vs lightweight CSFO extraction",y=1.02)
        fig.tight_layout(); fig.savefig("fig_compare.png",dpi=150,bbox_inches="tight")
        print("\nwrote comparison.csv, comparison_report.md, fig_compare.png")
    except Exception as e:
        print(f"\n(figure skipped: {e}) — wrote comparison.csv, comparison_report.md")

if __name__=="__main__":
    main()
