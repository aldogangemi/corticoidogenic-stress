#!/usr/bin/env python3
"""
run_polanyi_csfo.py -- facilitation harness for ITERATIVE launching of POLANYI++ v25
(the real polanyi_launcher.py orchestrator) with CSFO as the ontology prior U, to
produce ONE XKG per input:
  * one XKG per VERBAL REPORT  (each *.txt in --reports, e.g. the LiveJournal corpus)
  * one XKG per ISIT APPLICATION (each row of --isit-csv, rendered to text S)

It does NOT re-implement POLANYI++ — it shells out to the uploaded launcher, which
runs Phase A (scoring) → Phase B (pol_assembler → OIS) → Phase C (extraction) and
now includes m30-DEEPJUDGE (T22-JUDGE) and m25-DARKSIDE.

Prerequisites in --pol-dir (the POLANYI++ install):
  polanyi_launcher.py, polanyicxteng_v25_marked.md, pol_assembler.py,
  polanyi_preassessment.md, scoring_input_template.md, polanyiontoreg.ttl
Provider key: ANTHROPIC_API_KEY or GEMINI_API_KEY (or pass --api-key).
CSFO prior: --ontology csf.ttl (import closure of the CSF network v3.4).

Config for the multilayer stress reading (v25):
  H = b22-DIAGNOSIS, b04-TEMPORALBINDING, b02-BIOMARKERBINDING, b05-IMPLICATURE,
      b06-IMPACT, b09-CAUSALITY
  M = m20-ONTOLOGYRAG(U=CSFO), m24-ISITPROFILE, m17-ABDUCTIVE-DIAGNOSIS,
      m25-DARKSIDE, m30-DEEPJUDGE
  T = T16-PROFILE (+ T22-JUDGE via m30) multilayer L1->L8 with abductive completion

Usage:
  # verbal reports -> one XKG each
  python3 run_polanyi_csfo.py --pol-dir ~/polanyi --ontology ontology/csf.ttl \\
      --reports ljcorpus --out xkg_reports --provider anthropic
  # ISIT applications (dataset cases) -> one XKG each
  python3 run_polanyi_csfo.py --pol-dir ~/polanyi --ontology ontology/csf.ttl \\
      --isit-csv isit_cases.csv --out xkg_isit --provider anthropic
  # preview the exact launcher commands without running:
  python3 run_polanyi_csfo.py --pol-dir ~/polanyi --reports ljcorpus --dry-run
"""
import argparse, glob, os, subprocess, sys, csv, textwrap, shlex

H = "b22,b04,b02,b05,b06,b09"
M = "m20,m24,m17,m25,m30"
TASK = ("T16-PROFILE: produce a MULTILAYER corticoidogenic stress reading of S under "
        "the CSFO prior U. Bind exactly one corticoidogenic frame (A-H) + any protective "
        "frames (P1-P4) via a csf:CSF_Situation, gated by m30-DEEPJUDGE (drop Unwarranted, "
        "TRANSIENT-flag Weak). m24-ISITPROFILE: five ISIT dimensions. m17-ABDUCTIVE-DIAGNOSIS: "
        "for the physiological layers not observable in text (L4 autonomic, L5 endocrine, "
        "L6 inflammatory/metabolic, L7 molecular) abduce the licensed csc: cascade-step "
        "occurrences from observed L1-L3/L8 content; tag pol:evidenceStatus (Observed|Abduced), "
        "pol:plausibility, licensing cue; grade plausibility down with inferential distance. "
        "m25-DARKSIDE: reify coherence faults as dark: triples. Tag every item on channel "
        "(Internalized|Externalized) and epistemicStatus (Explicit|Implicit).")

def check_poldir(pol):
    # files the launcher loads from --pol-dir for a standard extraction run
    need = ["polanyi_launcher.py","polanyicxteng_v25_marked.md","pol_assembler.py",
            "polanyi_preassessment.md","scoring_input_template.md",
            "propbank_amr_index_compact.json","polanyiontoreg.ttl"]
    missing=[f for f in need if not os.path.exists(os.path.join(pol,f))]
    # m30 assembler-gap check: warn if pol_assembler predates m30 (OIS may drop m30)
    pa=os.path.join(pol,"pol_assembler.py")
    if os.path.exists(pa) and "m30" not in open(pa,encoding="utf-8",errors="ignore").read():
        print("  [warn] pol_assembler.py has no 'm30' reference -> the assembler may drop "
              "the m30-DEEPJUDGE OIS section (same class of gap the skill documents for "
              "m23/m24/m25). Apply the m30 assembler patch or inject the m30 section manually.")
    # ontoreg judge: prefix check (m30 vocabulary)
    org=os.path.join(pol,"polanyiontoreg.ttl")
    if os.path.exists(org) and "judge:" not in open(org,encoding="utf-8",errors="ignore").read():
        print("  [warn] polanyiontoreg.ttl declares no 'judge:' prefix -> add the m30 "
              "judge: namespace so DEEPJUDGE triples validate.")
    return missing

def launch(pol, text_file, out, u_text, provider, api_key, extra, dry):
    cmd=["python3", os.path.join(pol,"polanyi_launcher.py"),
         "--text-file", text_file, "--heuristics", H, "--methods", M, "--task", TASK,
         "--provider", provider, "--pol-dir", pol, "--output", out,
         "--output-yaml", out.replace(".ttl",".scoring.yaml")]
    if u_text: cmd += ["--ontology-prior-text", u_text]   # CSFO U as content (NOT csf.ttl, which is import-only)
    if api_key: cmd += ["--api-key", api_key]
    cmd += extra
    if dry:
        print("  "+" ".join(shlex.quote(c) if (" " in c and len(c)<60) else (c if len(c)<60 else c[:57]+"…") for c in cmd))
        return 0
    return subprocess.run(cmd).returncode

def isit_row_to_text(row):
    """Render a dataset ISIT-application case as a compact S text."""
    kv = "; ".join(f"{k}={v}" for k,v in row.items() if str(v).strip() and k not in ("",))
    return f"Case for ISIT application (structured record). {kv}"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pol-dir",required=True,help="POLANYI++ install dir (launcher + v25 + assembler + support)")
    ap.add_argument("--ontology",default="csf_u_profile_v34.md",
        help="CSFO prior U as CONTENT (condensed csf_u_profile_v34.md [default], or full "
             "csf_u_v34.ttl). NOT csf.ttl (import-only).")
    ap.add_argument("--reports",default=None,help="dir of verbal-report *.txt files")
    ap.add_argument("--isit-csv",default=None,help="CSV of ISIT-application cases (one XKG per row)")
    ap.add_argument("--out",default="xkg_out")
    ap.add_argument("--provider",default="anthropic",choices=["anthropic","gemini"])
    ap.add_argument("--api-key",default=None)
    ap.add_argument("--extra",default="",help="extra launcher flags, quoted")
    ap.add_argument("--dry-run",action="store_true")
    a=ap.parse_args()

    if not a.dry_run:
        miss=check_poldir(a.pol_dir)
        if miss: sys.exit("Missing in --pol-dir: "+", ".join(miss)+
                          "\n(SKILL.md alone is insufficient; sync the full POLANYI++ install.)")
        if a.provider=="anthropic" and not (a.api_key or os.environ.get("ANTHROPIC_API_KEY")):
            sys.exit("Set ANTHROPIC_API_KEY or pass --api-key.")
    os.makedirs(a.out,exist_ok=True)
    extra=a.extra.split() if a.extra else []
    u_text = open(a.ontology,encoding="utf-8").read() if (a.ontology and os.path.exists(a.ontology)) else None
    if a.ontology and not u_text and not a.dry_run:
        sys.exit(f"CSFO U file not found: {a.ontology} (use csf_u_profile_v34.md or csf_u_v34.ttl).")
    n=0
    if a.reports:
        posts=sorted(glob.glob(os.path.join(a.reports,"*.txt")))
        print(f"[verbal reports] {len(posts)} inputs | H={H} | M={M} | U={a.ontology}")
        for p in posts:
            pid=os.path.splitext(os.path.basename(p))[0]
            launch(a.pol_dir,p,os.path.join(a.out,f"{pid}.ttl"),u_text,a.provider,a.api_key,extra,a.dry_run); n+=1
    if a.isit_csv:
        rows=list(csv.DictReader(open(a.isit_csv)))
        print(f"[ISIT applications] {len(rows)} cases from {a.isit_csv}")
        tmpdir=os.path.join(a.out,"_isit_inputs"); os.makedirs(tmpdir,exist_ok=True)
        for i,row in enumerate(rows):
            base=row.get("id") or row.get("subject_id") or row.get("uid") or "case"
            cid=f"{base}_{i}"
            tf=os.path.join(tmpdir,f"{cid}.txt"); open(tf,"w").write(isit_row_to_text(row))
            launch(a.pol_dir,tf,os.path.join(a.out,f"isit_{cid}.ttl"),u_text,a.provider,a.api_key,extra,a.dry_run); n+=1
    if not (a.reports or a.isit_csv):
        sys.exit("Provide --reports and/or --isit-csv.")
    print(f"\n{'(dry-run) ' if a.dry_run else ''}{n} XKG job(s). Then run deepjudge/consensus over --out/*.ttl.")

if __name__=="__main__":
    main()
