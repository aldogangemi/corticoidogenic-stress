#!/usr/bin/env python3
"""
frame_distribution_analysis.py -- evaluate the CSFO frame layer the way the RESEARCH
HYPOTHESES actually frame it, NOT as single-label classification.

Hypotheses (Gangemi/Lucifora):
  H0  no post MUST have one dominant corticoidogenic frame.
  H1  the 8 frames are theoretically SEPARATED (literature-grounded) and used as
      DIFFERENTIAL CLUSTERS — so agreement must be judged on the co-activation
      DISTRIBUTION, not the argmax.
  H2  the ABSENCE of a dominant frame is a signal — a hint to MODERATOR presence
      (here proxied by co-activated protective frames P1–P4).

So we measure, per method and between methods:
  • frame DISTRIBUTION agreement  — cosine + Jensen–Shannon over the A–H weight vector
  • concentration / dominance     — Berger–Parker BP=max(p); #co-active frames n_cort
  • H2 test                       — Spearman(n_cort, M) (predict +) and Spearman(BP, M)
                                    (predict −), where M = #protective frames per post
  • cross-method consistency      — do methods agree on WHICH posts are diffuse (BP) and
                                    on moderator load (M)?
  • co-activation structure       — empirical corticoidogenic co-occurrence matrix
                                    (descriptive; compare to ontology compoundsWith)

NOTE on granularity: master_long collapses frame weight to lead=1.0 / secondary=0.5,
so BP is a near-deterministic function of set size; n_cort is the transparent diffuseness
index. Finer verdict/confidence weights (in the raw XKGs/JSONs) would give a truer BP.

Usage: python3 frame_distribution_analysis.py --long master_long_fixed.csv --onto ontology
"""
import argparse, csv, os, re, glob
import numpy as np
from collections import defaultdict, Counter

CORT=list("ABCDEFGH"); PROT=["P1","P2","P3","P4"]
def spearman(x,y):
    x=np.asarray(x,float);y=np.asarray(y,float)
    m=~(np.isnan(x)|np.isnan(y)); x,y=x[m],y[m]          # drop NaN pairs (unmeasured)
    if len(x)<4: return np.nan
    rx=np.argsort(np.argsort(x)).astype(float);ry=np.argsort(np.argsort(y)).astype(float)
    rx-=rx.mean();ry-=ry.mean();d=np.sqrt((rx@rx)*(ry@ry));return float(rx@ry/d) if d>0 else np.nan
def jsd(p,q):
    p=np.asarray(p,float);q=np.asarray(q,float)
    if p.sum()==0 or q.sum()==0: return np.nan
    p=p/p.sum();q=q/q.sum();m=0.5*(p+q)
    def kl(a,b):
        mask=a>0; return float(np.sum(a[mask]*np.log(a[mask]/b[mask])))
    return 0.5*kl(p,m)+0.5*kl(q,m)          # in nats, 0..ln2
def cosine(p,q):
    p=np.asarray(p,float);q=np.asarray(q,float)
    if np.linalg.norm(p)==0 or np.linalg.norm(q)==0: return np.nan
    return float(p@q/(np.linalg.norm(p)*np.linalg.norm(q)))

def load(path):
    D=defaultdict(lambda:{"cw":{},"prot":set(),"isit":{},"mod":Counter()})
    for r in csv.DictReader(open(path)):
        k=(r["source"],r["item_id"])
        if r["construct_type"]=="frame":
            f=r["construct"]
            try: v=float(r["value"])
            except: v=0.5
            if f in PROT: D[k]["prot"].add(f)
            elif f in CORT: D[k]["cw"][f]=max(D[k]["cw"].get(f,0),v)
        elif r["construct_type"]=="isit":
            try: D[k]["isit"][r["construct"]]=float(r["value"])
            except: pass
        elif r["construct_type"]=="moderator":
            D[k]["mod"][r["construct"]]+=1          # construct = amplifier | buffer
    return D

def mod_counts(d):
    """(amplifier, buffer) counts. If the source has explicit mod: rows use them;
    else (light schema has no amplifiers) fall back to protective frames as buffers,
    amplifier=NA."""
    if d["mod"]:
        return d["mod"].get("amplifier",0), d["mod"].get("buffer",0), True
    return None, len(d["prot"]), False              # amp NA, buffers via protective proxy

def pvec(cw): return np.array([cw.get(f,0.0) for f in CORT])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--long",default="master_long_fixed.csv"); ap.add_argument("--onto",default="ontology")
    ap.add_argument("--out",default="master_out")
    a=ap.parse_args()
    D=load(a.long)
    srcs=sorted({s for (s,_) in D})
    R=["# Frame layer evaluated as co-activation DISTRIBUTION + moderator hint (H0–H2)\n"]

    # ---- per-method: concentration and H2 (diffuseness -> moderators), split amp/buffer ----
    R.append("## Per-method: does LOW dominance co-occur with MODERATORS? (H2)")
    R.append("_Moderators = amplifiers AND buffers (mod: module). Amplifiers spread activation "
             "(predict diffuse⇒more frames); buffers concentrate/reduce. Tested separately._\n")
    for s in srcs:
        items=[it for (ss,it) in D if ss==s]
        # a source that emits ANY amplifier row CAN emit them -> per-post absence is a real 0;
        # a source with none (structurally) -> amplifier is NA (unmeasurable), never 0.
        src_has_amp=any(D[(s,it)]["mod"].get("amplifier",0) for it in items)
        ncort=[]; BP=[]; amp=[]; buf=[]
        for it in items:
            d=D[(s,it)]; p=pvec(d["cw"])
            if p.sum()==0: continue
            ncort.append(int((p>0).sum())); BP.append(float(p.max()/p.sum()))
            amp.append(d["mod"].get("amplifier",0) if src_has_amp else np.nan)
            buf.append(len(d["prot"]))                    # buffers via protective frames (all methods)
        n_amp=sum(1 for x in amp if x==x and x>0)
        line=f"- **{s}** (n={len(ncort)}, mean #frames={np.mean(ncort):.2f}): "
        if src_has_amp:
            line+=(f"AMPLIFIERS in {n_amp}/{len(amp)} posts (mean {np.nansum(amp)/len(amp):.2f}/post) — "
                   f"ρ(#frames,#amp)=**{spearman(ncort,amp):+.2f}** (H2:+), "
                   f"ρ(BP,#amp)=**{spearman(BP,amp):+.2f}** (H2:−)"
                   +("  ⚠ underpowered" if n_amp<8 else "")+"; ")
        else:
            line+="amplifiers NA (schema emits none); "
        line+=(f"BUFFERS(protective) in {sum(1 for x in buf if x)}/{len(buf)} posts — "
               f"ρ(#frames,#buf)=**{spearman(ncort,buf):+.2f}**, ρ(BP,#buf)=**{spearman(BP,buf):+.2f}**")
        R.append(line)
    R.append("\n_H2 (amplifier form): diffuse frame activation ⇒ amplifiers ⇒ positive ρ(#frames,#amp), "
             "negative ρ(BP,#amp). Testable only where amplifiers exist (full pipeline); the light "
             "schema emits buffers only, so its 'moderator' column is buffers-as-protective._")

    # ---- between-method: distribution agreement (H1) ----
    R.append("\n## Between-method: agreement on the DISTRIBUTION, not the argmax (H1)")
    for i in range(len(srcs)):
        for j in range(i+1,len(srcs)):
            s1,s2=srcs[i],srcs[j]
            sh=sorted({it for (s,it) in D if s==s1} & {it for (s,it) in D if s==s2})
            cs=[]; js=[]; bp1=[];bp2=[];m1=[];m2=[]
            for it in sh:
                p=pvec(D[(s1,it)]["cw"]); q=pvec(D[(s2,it)]["cw"])
                if p.sum()==0 or q.sum()==0: continue
                cs.append(cosine(p,q)); js.append(jsd(p,q))
                bp1.append(p.max()/p.sum()); bp2.append(q.max()/q.sum())
                m1.append(len(D[(s1,it)]["prot"])); m2.append(len(D[(s2,it)]["prot"]))
            R.append(f"- **{s1} vs {s2}** (n={len(cs)}): distribution cosine **{np.nanmean(cs):.2f}**, "
                     f"mean JSD **{np.nanmean(js):.2f}** nats (0=identical, 0.69=disjoint); "
                     f"agree on diffuseness ρ(BP)=**{spearman(bp1,bp2):+.2f}**, "
                     f"on moderator load ρ(#mod)=**{spearman(m1,m2):+.2f}**")

    # ---- co-activation structure (H1 differential clusters) ----
    R.append("\n## Corticoidogenic co-activation matrix (full_sonnet) vs ontology compoundsWith")
    ref="full_sonnet" if "full_sonnet" in srcs else srcs[0]
    co=np.zeros((8,8)); idx={f:i for i,f in enumerate(CORT)}
    for (s,it),d in D.items():
        if s!=ref: continue
        act=[f for f in CORT if d["cw"].get(f,0)>0]
        for x in act:
            for y in act:
                if x!=y: co[idx[x],idx[y]]+=1
    # ontology compoundsWith pairs
    comp=set()
    try:
        from rdflib import Graph
        g=Graph()
        for f in glob.glob(os.path.join(a.onto,"*frames*.ttl")):
            try: g.parse(f,format="turtle")
            except: pass
        FRN={'ChronicSubordinationFrame':'A','UncontrollableThreatFrame':'B','CaregivingEntrapmentFrame':'C',
        'EarlyLifeProgrammingFrame':'D','ChronicIdentityThreatFrame':'E','CircadianMisalignmentFrame':'F',
        'SocialDisconnectionFrame':'G','SocioeconomicStrainFrame':'H'}
        def ln(u): return re.split(r'[#/]',str(u))[-1]
        for sb,p,o in g:
            if ln(p)=="compoundsWithFrame" and ln(sb) in FRN and ln(o) in FRN:
                comp.add(frozenset([FRN[ln(sb)],FRN[ln(o)]]))
    except Exception: pass
    R.append("```")
    R.append("     "+" ".join(f"{f:>3}" for f in CORT))
    for i,f in enumerate(CORT):
        R.append(f"{f:>3}  "+" ".join(f"{int(co[i,j]):>3}" for j in range(8)))
    R.append("```")
    if comp:
        # LIFT = P(x,y)/(P(x)P(y)) — marginal-corrected (raw counts are base-rate confounded:
        # frequent frames co-occur by chance). Lift>1 = co-activate MORE than independent.
        postsets=[{f for f in CORT if d["cw"].get(f,0)>0}
                  for (s,it),d in D.items() if s==ref]
        Np=len(postsets); marg={f:sum(f in ps for ps in postsets)/Np for f in CORT}
        def lift(x,y):
            pxy=sum({x,y}<=ps for ps in postsets)/Np
            return pxy/(marg[x]*marg[y]) if marg[x]*marg[y]>0 else float("nan")
        clift=[lift(*sorted(fs)) for fs in comp if len(fs)==2]
        olift=[lift(CORT[i],CORT[j]) for i in range(8) for j in range(i+1,8)
               if frozenset([CORT[i],CORT[j]]) not in comp]
        tops=sorted([((CORT[i],CORT[j]),lift(CORT[i],CORT[j])) for i in range(8) for j in range(i+1,8)],
                    key=lambda t:-(t[1] if t[1]==t[1] else -9))[:5]
        R.append(f"Marginal-corrected LIFT (N={Np}): ontology compoundsWith pairs "
                 f"{sorted(sorted(x) for x in comp)} mean lift **{np.nanmean(clift):.2f}** vs other pairs "
                 f"**{np.nanmean(olift):.2f}** → "
                 +("declared compounds co-activate ABOVE chance." if np.nanmean(clift)>np.nanmean(olift)
                   else "declared compounds do NOT co-activate above chance (theory–data tension).")
                 +f" Top empirical lifts: {[(p,round(l,2)) for p,l in tops]}. "
                 "(small-N: lift estimates are noisy.)")

    R.append("\n## Reading\nUnder H0–H2 the argmax κ is the wrong yardstick. What matters: (1) methods "
        "agree on the co-activation DISTRIBUTION (cosine/JSD above); (2) they agree on WHICH posts are "
        "diffuse (ρ(BP)); (3) diffuseness tracks moderator presence (H2). A negative BP↔moderator "
        "correlation with positive #frames↔moderator is the empirical signature of 'no dominant frame ⇒ "
        "look for moderators'. The co-activation matrix tests whether frames behave as literature-"
        "predicted differential-yet-compounding clusters.")
    open(os.path.join(a.out,"frame_distribution_analysis.md"),"w").write("\n".join(R))
    print("\n".join(R))

if __name__=="__main__":
    main()
