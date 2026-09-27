#!/usr/bin/env python3
"""
run_lightweight_csfo.py -- the LIGHTWEIGHT (Option-B) CSFO extractor over the LJ
corpus, as a batch counterpart to the full POLANYI++ launcher. One condensed
Anthropic call per text (CSFO U-profile injected as prior, not the full OIS),
producing the same comparison targets: primary frame (A–H) + m30-lite verdict,
ISIT (BOTH the official semantic-space dims AND the CSFO dims), a multilayer
reading (L1–L8, Observed/Abduced), and a DARKSIDE flag.

This is the cheap/fast path; pair it with the full-launcher XKGs and
compare_full_vs_lightweight.py to quantify what the heavy pipeline adds.

Dependency-free (stdlib urllib). Needs ANTHROPIC_API_KEY (or --api-key).
Usage:
  # deepest cheap result (strong reasoner + minimal orchestration):
  python3 run_lightweight_csfo.py --reports ljcorpus --u csf_u_profile_v34.md \\
      --out lightweight_out --model claude-opus-5
  # model-matched arm for the controlled full-vs-light comparison:
  python3 run_lightweight_csfo.py --reports ljcorpus --u csf_u_profile_v34.md \\
      --out lightweight_out_sonnet --model claude-sonnet-5
"""
import argparse, glob, json, os, re, sys, time, ssl, urllib.request, urllib.error

def _ssl_context():
    """macOS python.org builds ship without an OS CA bundle -> stdlib urllib
    raises CERTIFICATE_VERIFY_FAILED. Use certifi's bundle if available (it is,
    whenever pip/requests are installed), then $SSL_CERT_FILE, then the default.
    We never disable verification."""
    cafile=os.environ.get("SSL_CERT_FILE")
    if not cafile:
        try:
            import certifi; cafile=certifi.where()
        except Exception:
            cafile=None
    try:
        return ssl.create_default_context(cafile=cafile)
    except Exception:
        return ssl.create_default_context()

_SSL=_ssl_context()

SCHEMA = """Output ONLY this JSON (no fences, no prose). Floats 0..1 except valence in [-1,1].
{"primaryFrame":{"id":"A-H","uri":"frm:...","label":"<=5 words","confidence":0.0},
 "frameVerdict":{"status":"Warranted|Weak|Unwarranted","warrant":"<=8 words"},
 "secondaryFrames":[{"id":"A-H","label":"<=5 words","confidence":0.0}],
 "protectiveFrames":[{"id":"P1-P4","label":"<=5 words","strength":0.0}],
 "moderators":[{"factor":"<=4 words","role":"amplifier|buffer","subtype":"cognitive|behavioral|social|contextual|genetic|developmental","strength":0.0}],
 "gasPhase":"Alarm|Resistance|Exhaustion|Adaptation|Recovery",
 "isit_csfo":{"overallIntensity":0.0,"controllability":0.0,"chronicity":0.0,"socialEmbeddedness":0.0,"agencyLevel":0.0},
 "isit_semantic":{"valence":0.0,"arousal":0.0,"intimacy":0.0,"formality":0.0,"agency":0.0,"directedness":0.0},
 "multilayer":[{"step":"csc:...","layer":"L1|L2|L3|L4|L5|L6|L7|L8","status":"Observed|Abduced","plausibility":0.0,"cue":"<=8 words"}],
 "bridges":[{"from":"L2|L3|L4|L5|L6|L7|amp:<factor>|buf:<factor>","to":"L3|L4|L5|L6|L7|out:<outcome>","relation":"directlyPrecedes|feedsForward|feedsBack|leadsToOutcome|amplifies|buffers","plausibility":0.0,"cue":"<=8 words","citation":"PMID-or-empty"}],
 "darkside":{"contradictionType":"Encapsulated|Vain|null","commitment":"<=8 words","exclusion":"<=8 words","delegationRisk":"TRUSTLESS|SUPERVISED|UNSAFE"}}
INSTRUCTIONS: bind exactly one corticoidogenic frame A-H (m30-DEEPJUDGE gate: Warranted if directly cued, Weak if inferred, Unwarranted->pick next best). Score BOTH ISIT sets. For L4-L7 physiology (not observable in text) mark status=Abduced with plausibility graded DOWN by inferential distance + the licensing cue. APPRAISAL (L2): the author's cognitive appraisal is a FIRST-CLASS L2 step, usually OBSERVED (stated in the text), not abduced -- emit it in multilayer with layer=L2 and step csc:PrimaryAppraisalType (harm/threat/challenge), csc:SecondaryAppraisalType (coping/controllability) or csc:ThreatAppraisalType, and BRIDGE it as the mediator: L1 situation -> L2 appraisal -> L3/L4 response (Lazarus & Folkman 1984). Appraisal is a causal LINK, distinct from moderators (which modulate it) and ISIT (which describes it). Report one DARKSIDE constancy violation only if a commitment is excluded by the case's own facts, else contradictionType=null. ALWAYS assess mod: moderators, listing every one actually cued: AMPLIFIERS (mod:Amplifier — e.g. early-adversity history, substance use, ruminative style, social isolation, sleep deprivation, sedentary lifestyle) that intensify/spread the cascade, and BUFFERS (mod:Buffer — e.g. high social support, meaning-making capacity, high self-efficacy, perceived controllability, secure attachment, safe housing) that dampen it; [] only if none are cued. moderators are the mod: factor layer (distinct from the P1-P4 protective FRAMES). BRIDGES (cross-layer causal entrenchment as POSSIBILIA, not asserted causation): wire the layers together — each abduced physiological step directlyPrecedes the next-deeper layer (L4->L5->L6->L7); the terminal step leadsToOutcome the most likely L8 outcome (out:<name>); each amplifier amplifies (each buffer buffers) the SPECIFIC step it acts on (amp:<factor> -> L5 etc.); add feedsBack only where the literature supports a downstream->upstream loop (e.g. L6 inflammation -> L5 HPA). Every bridge is a typical/possible pathway: grade plausibility DOWN by inferential distance and add a PubMed citation where you know one (else ""). Return [] if fewer than two layers are present."""

_PREFILL_OK=True   # some models (e.g. opus-5) reject assistant-message prefill; auto-disable on first 400

def call(model, key, system, user, max_tokens=4096, retries=5, prefill="{"):
    """Prefill ('{') forces JSON-only start WHEN the model supports it; if the API
    rejects it (400 'does not support assistant message prefill'), we drop prefill
    globally and rely on loads_lenient. So at most ONE probe call is 'wasted', not
    one per text."""
    global _PREFILL_OK
    for attempt in range(retries):
        pf = prefill if (_PREFILL_OK and prefill) else None
        msgs=[{"role":"user","content":user}]
        if pf: msgs.append({"role":"assistant","content":pf})
        body = json.dumps({"model": model, "max_tokens": max_tokens,
                           "system": system, "messages":msgs}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
              headers={"content-type":"application/json","x-api-key":key,"anthropic-version":"2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=180, context=_SSL) as r:
                data = json.loads(r.read())
                txt="".join(b.get("text","") for b in data.get("content",[]))
                if data.get("stop_reason")=="max_tokens":
                    print("    [warn] hit max_tokens — output may be truncated; raise --max-tokens")
                return (pf+txt) if pf else txt   # prefill isn't echoed; prepend it back
        except urllib.error.HTTPError as e:
            msg=e.read().decode()
            if e.code==400 and pf and "prefill" in msg.lower():
                _PREFILL_OK=False
                print("    [info] model rejects prefill — switching to JSON-instructed parsing")
                continue                         # retry this same text without prefill
            if e.code in (429,500,529) and attempt < retries-1:
                time.sleep(2**attempt * 3); continue
            raise RuntimeError(f"HTTP {e.code}: {msg[:200]}")
    raise RuntimeError("retries exhausted")

def loads_lenient(s):
    """Tolerate code fences, prose around the object, and trailing commas; slice the
    first balanced {...} so a stray token before/after JSON can't break the parse."""
    s=s.strip()
    s=re.sub(r'^```(?:json)?\s*','',s); s=re.sub(r'\s*```$','',s)
    i=s.find('{')
    if i>=0:                                    # balanced-brace slice (quote/escape aware)
        depth=0; instr=False; esc=False
        for j in range(i,len(s)):
            c=s[j]
            if instr:
                if esc: esc=False
                elif c=='\\': esc=True
                elif c=='"': instr=False
            elif c=='"': instr=True
            elif c=='{': depth+=1
            elif c=='}':
                depth-=1
                if depth==0: s=s[i:j+1]; break
    s=re.sub(r',(\s*[}\]])', r'\1', s)          # kill trailing commas
    return json.loads(s)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--reports",required=True); ap.add_argument("--u",default="csf_u_profile_v34.md")
    ap.add_argument("--out",default="lightweight_out"); ap.add_argument("--api-key",default=None)
    # NOTE: "lightweight" = orchestration (one condensed call), NOT the model. The
    # model is an orthogonal knob: a STRONGER reasoner behind minimal steering probes
    # the upper bound of the cheap path (cell D). Default = strongest reasoner. For the
    # clean "what does the full pipeline buy?" comparison, ALSO run one arm at whatever
    # model polanyi_launcher.py uses (cell C) so the delta isn't confounded by model.
    ap.add_argument("--model",default="claude-opus-5",
                    help="claude-opus-5 (default, deepest) | claude-sonnet-5 (cheaper/faster) "
                         "| match the full launcher's model for the controlled comparison")
    ap.add_argument("--sleep",type=float,default=1.0); ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--max-tokens",type=int,default=4096,help="raise if you see truncation warnings")
    a=ap.parse_args()
    key=a.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key and not a.dry_run: sys.exit("Set ANTHROPIC_API_KEY or pass --api-key.")
    U=open(a.u,encoding="utf-8").read() if os.path.exists(a.u) else ""
    system=("You are a clinical stress ontology analyst implementing POLANYI++ v24 Option-B "
            "(condensed) with CSFO as the ontology prior U (m20-ONTOLOGYRAG). Respond with valid "
            "JSON only.\n\n## U — CSFO prior\n"+U+"\n\n"+SCHEMA)
    os.makedirs(a.out,exist_ok=True)
    posts=sorted(glob.glob(os.path.join(a.reports,"*.txt")),
                 key=lambda p:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',os.path.basename(p))])
    print(f"[lightweight] {len(posts)} texts | model={a.model} | U={a.u}")
    ok=0
    for p in posts:
        pid=os.path.splitext(os.path.basename(p))[0]; out=os.path.join(a.out,f"{pid}.json")
        if a.dry_run: print(f"  would extract {pid}"); continue
        raw=None
        try:
            raw=call(a.model,key,system,f"## S — verbal report\n\n{open(p,encoding='utf-8').read()}",
                     max_tokens=a.max_tokens)
            js=loads_lenient(raw)
            js["_pid"]=pid; json.dump(js,open(out,"w"),indent=2); ok+=1
            print(f"  {pid}: {js.get('primaryFrame',{}).get('id','?')} "
                  f"({js.get('frameVerdict',{}).get('status','?')})")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")
            # keep the raw text so a parse failure is diagnosable (not silently lost)
            json.dump({"_pid":pid,"error":str(e),"raw":(raw or "")[:4000]},open(out,"w"))
        time.sleep(a.sleep)
    print(f"\n{ok}/{len(posts)} extracted -> {a.out}/*.json")

if __name__=="__main__":
    main()
