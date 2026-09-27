#!/usr/bin/env python3
"""
run_panel.py — launch the five layer adjudicators, each on its own backbone.

    python3 run_panel.py --depth L1 --round 1 --dry-run
    python3 run_panel.py --depth L1 --round 1
    python3 run_panel.py --depth L1 --round 2 --prior out/verdicts.L1.r1.jsonl

Backbone assignment lives in backbones.json so the model<->layer mapping is
recorded with the run rather than buried in code. Rotate it across runs: if the
same layer always gets the same backbone, layer effects and backbone effects are
confounded and the panel's independence claim is unfalsifiable.

Round 1: every agent judges independently. No cross-talk.
Round 2 (Delphi): each agent re-judges after seeing the OTHER AGENTS' DEFEATERS
ONLY -- never their verdicts or confidences. Verdict exchange is what produces
conformity cascades; reason exchange is what produces revision. Round-2 output is
kept in a separate file so both rounds can be aggregated and compared.
"""
from __future__ import annotations
import argparse, json, os, random, re, sys, time
from collections import defaultdict

AGENTS = ["A1_SOCIAL", "A2_PSYCH", "A3_ENDOCRINE", "A4_NEUROIMMUNE", "A5_MOLECULAR"]
BATCH = int(os.environ.get('PANEL_BATCH', '12'))
# Reasoning/thinking tokens are billed against the same budget as visible output.
# 4000 was enough for the JSON but not for the JSON *plus* a reasoning trace, which
# is why thinking-capable models returned zero text while Haiku 4.5 did not.
MAX_TOKENS = int(os.environ.get('PANEL_MAX_TOKENS', '16000'))


class EmptyCompletion(RuntimeError):
    """Model returned no usable text. Distinguished from transport errors so a
    single bad batch degrades to missing data instead of killing the fold."""
VALID_VERDICT = {"SUPPORTED", "UNDERDETERMINED", "CONTRADICTED", "OUT_OF_SCOPE"}
VALID_WF = {"WELLFORMED", "REFERENT_MISMATCH", "TIMESCALE_NONCOMPOSABLE", "INDIVIDUATION_CLASH"}

DEFAULT_BACKBONES = {
    "A1_SOCIAL":      {"provider": "anthropic", "model": "<model-a>"},
    "A2_PSYCH":       {"provider": "openai",    "model": "<model-b>"},
    "A3_ENDOCRINE":   {"provider": "google",    "model": "<model-c>"},
    "A4_NEUROIMMUNE": {"provider": "mistral",   "model": "<model-d>"},
    "A5_MOLECULAR":   {"provider": "local",     "model": "<model-e>"},
}


# ------------------------------------------------------------------ providers
def call_model(provider, model, system, user, dry_run=False):
    """Single completion. Providers are thin on purpose: swap in your own client."""
    if dry_run:
        return None
    if provider == "anthropic":
        import anthropic
        c = anthropic.Anthropic()
        kw = dict(model=model, max_tokens=MAX_TOKENS, system=system,
                  messages=[{"role": "user", "content": user}])
        try:
            r = c.messages.create(**kw, thinking={"type": "disabled"})
        except TypeError:
            r = c.messages.create(**kw)
        except Exception as e:
            if "thinking" not in str(e):
                raise
            r = c.messages.create(**kw)
        txt = "".join(b.text for b in r.content if b.type == "text")
        if not txt:
            kinds = sorted({b.type for b in r.content})
            raise EmptyCompletion(
                f"no text block (blocks={kinds}, stop_reason={r.stop_reason}); "
                f"raise MAX_TOKENS or disable reasoning")
        return txt
    if provider == "openai":
        from openai import OpenAI
        c = OpenAI()
        r = c.chat.completions.create(model=model, max_completion_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        txt = r.choices[0].message.content
        if not txt:
            raise EmptyCompletion(
                f"empty content (finish_reason={r.choices[0].finish_reason}); "
                f"reasoning tokens likely consumed the budget")
        return txt
    OPENAI_COMPAT = {
        "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        "cerebras":   ("https://api.cerebras.ai/v1",     "CEREBRAS_API_KEY"),
        "openrouter": ("https://openrouter.ai/api/v1",   "OPENROUTER_API_KEY"),
        "together":   ("https://api.together.xyz/v1",    "TOGETHER_API_KEY"),
        "mistral":    ("https://api.mistral.ai/v1",      "MISTRAL_API_KEY"),
        "cohere":     ("https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY"),
        "sambanova":  ("https://api.sambanova.ai/v1",     "SAMBANOVA_API_KEY"),
        "nvidia":     ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
        "cloudflare": (os.environ.get("CLOUDFLARE_BASE_URL", ""), "CLOUDFLARE_API_KEY"),
        "local":      (os.environ.get("LOCAL_BASE_URL", "http://localhost:8000/v1"), "LOCAL_API_KEY"),
    }
    if provider in OPENAI_COMPAT:
        from openai import OpenAI
        base, envvar = OPENAI_COMPAT[provider]
        c = OpenAI(base_url=base, api_key=os.environ.get(envvar, "none"))
        r = c.chat.completions.create(model=model, messages=[
            {"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content
    if provider == "google":
        import urllib.request, ssl as _ssl, json as _json
        try:
            import certifi
            _ctx = _ssl.create_default_context(
                cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
        except ImportError:
            _ctx = _ssl.create_default_context()
        key = os.environ["GEMINI_API_KEY"]
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        cfg = {"maxOutputTokens": MAX_TOKENS, "temperature": 0}
        # Gemini 3.x uses thinkingLevel, 2.5 uses thinkingBudget; send the one
        # that matches and let an unknown-field 400 fall back to no config.
        think = ({"thinkingLevel": "low"} if model.startswith("gemini-3")
                 else {"thinkingBudget": 0})
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": dict(cfg, thinkingConfig=think),
        }
        body = _json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers={
            "x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180, context=_ctx) as r:
                j = _json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            payload["generationConfig"] = cfg          # retry without thinkingConfig
            req = urllib.request.Request(url, data=_json.dumps(payload).encode(),
                                         headers={"x-goog-api-key": key,
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180, context=_ctx) as r:
                j = _json.load(r)
        cand = (j.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts")
        if not parts:
            raise EmptyCompletion(
                f"no parts (finishReason={cand.get('finishReason')}); "
                f"thinking tokens consumed maxOutputTokens, or the response was blocked")
        return "".join(p.get("text", "") for p in parts)
    raise NotImplementedError(
        f"add a client for provider '{provider}' in call_model()")


# ------------------------------------------------------------------ prompting
def build_user_message(batch, prior_defeaters=None):
    lines = ["Judge each of the following items. Return a JSON array of objects, "
             "one per item, in the same order. Nothing else.\n"]
    for it in batch:
        lines.append(f"--- item {it['item_id']} [{it['item_type']}]")
        lines.append(f"claim: {it['claim']}")
        if it.get("span"):
            lines.append(f"source span: \"{it['span'].strip()}\"")
        if prior_defeaters and it["item_id"] in prior_defeaters:
            ds = prior_defeaters[it["item_id"]]
            lines.append("other specialists raised these considerations "
                         "(reasons only; their verdicts are withheld):")
            for d in ds:
                lines.append(f"  - {d}")
            lines.append("Re-judge. Change your verdict only if a consideration "
                         "gives you a reason to; agreement is not a goal.")
        lines.append("")
    return "\n".join(lines)


def parse_verdicts(raw, batch):
    """Tolerant parse: find the JSON array, coerce fields, drop invalid rows."""
    if raw is None:
        return []
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    ids = [b["item_id"] for b in batch]
    out = []
    for i, o in enumerate(arr):
        if not isinstance(o, dict):
            continue
        iid = o.get("item_id") or (ids[i] if i < len(ids) else None)
        if iid not in ids:
            continue
        v = str(o.get("verdict", "")).upper()
        w = str(o.get("wellformed", "")).upper()
        if v not in VALID_VERDICT or w not in VALID_WF:
            continue
        out.append(dict(item_id=iid, verdict=v, wellformed=w,
                        stance=str(o.get("stance", "NA")).upper(),
                        defeater=str(o.get("defeater", ""))[:400],
                        confidence=float(o.get("confidence", 0.0) or 0.0)))
    return out


# ------------------------------------------------------------------ main loop
def run_agent(agent, depth, items, backbone, prior_defeaters, args):
    system = open(os.path.join(args.out, "contexts", f"{agent}.{depth}.md")).read()
    rng = random.Random(hash((agent, depth, args.seed)) & 0xFFFF)
    order = list(items)
    rng.shuffle(order)                      # kill position effects within an agent
    results, misses, errors = [], 0, {}
    for k in range(0, len(order), BATCH):
        batch = order[k:k + BATCH]
        user = build_user_message(batch, prior_defeaters)
        got, err = [], None
        for attempt in range(3):
            try:
                raw = call_model(backbone["provider"], backbone["model"], system, user,
                                 dry_run=args.dry_run)
                err = None
            except Exception as e:                      # transport, quota, empty text
                err = f"{type(e).__name__}: {str(e)[:160]}"
                time.sleep(3 * (attempt + 1))
                continue
            got = parse_verdicts(raw, batch)
            if args.dry_run or got:
                break
            err = f"unparseable response ({len(raw or '')} chars)"
            time.sleep(2 * (attempt + 1))
        if err:
            errors[err] = errors.get(err, 0) + 1
        if args.dry_run:
            results.extend(dict(item_id=b["item_id"], verdict="DRYRUN", wellformed="DRYRUN",
                                stance="NA", defeater="", confidence=0.0) for b in batch)
            continue
        seen = {g["item_id"] for g in got}
        misses += len(batch) - len(seen)
        results.extend(got)
    if errors:
        print(f"    {sum(errors.values())} batch failure(s) for {agent}:")
        for e, n in sorted(errors.items(), key=lambda kv: -kv[1])[:3]:
            print(f"      x{n}  {e}")
    return results, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--depth", default="L1", choices=["L0", "L1", "L2"])
    ap.add_argument("--round", type=int, default=1, choices=[1, 2])
    ap.add_argument("--prior", help="round-1 verdicts file, required for round 2")
    ap.add_argument("--backbones", default="backbones.json")
    ap.add_argument("--fold", type=int, default=None,
                    help="fold index; appended to output filename when set")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="one real call per agent (first batch only) to verify every "
                         "backbone end to end before committing to a full run")
    args = ap.parse_args()

    backbones = (json.load(open(args.backbones))
                 if os.path.exists(args.backbones) else DEFAULT_BACKBONES)
    backbones = {k: v for k, v in backbones.items() if not k.startswith("_")}
    # fail loudly rather than silently running a five-persona panel on one model
    missing = [a for a in AGENTS
               if not (backbones.get(a) or {}).get("model")
               or str(backbones[a]["model"]).startswith("<")]
    if missing and not args.dry_run:
        sys.exit("backbones.json has no model set for: " + ", ".join(missing) +
                 "\nSet five distinct backbones. Running every agent on one model "
                 "collapses the panel into five personas sharing a prior, which is "
                 "the design Dawid-Skene cannot aggregate.")
    used = [backbones[a]["model"] for a in AGENTS if (backbones.get(a) or {}).get("model")]
    dupes = {m for m in used if used.count(m) > 1}
    if dupes:
        print("WARNING shared backbone across agents: " + ", ".join(sorted(dupes)) +
              " -- their errors are correlated; report the pairing with every posterior.")
    assignment = json.load(open(os.path.join(args.out, "assignment.json")))["assignment"]
    payloads = {a: [json.loads(l) for l in
                    open(os.path.join(args.out, "payloads", f"{a}.jsonl"))] for a in AGENTS}

    prior_defeaters = None
    if args.round == 2:
        if not args.prior:
            sys.exit("round 2 needs --prior")
        by_item = defaultdict(list)
        for l in open(args.prior):
            r = json.loads(l)
            if r.get("defeater"):
                by_item[r["item_id"]].append((r["agent"], r["defeater"]))
        prior_defeaters = by_item

    tag = f"{args.depth}.r{args.round}" + (f".f{args.fold}" if args.fold is not None else "")
    path = os.path.join(args.out, f"verdicts.{tag}.jsonl")
    with open(path, "w") as fh:
        for a in AGENTS:
            # in round 2, withhold the agent's own defeaters from itself
            pd = None
            if prior_defeaters is not None:
                pd = {i: [d for (ag, d) in v if ag != a] for i, v in prior_defeaters.items()}
                pd = {i: v for i, v in pd.items() if v}
            items_for_agent = payloads[a][:BATCH] if args.probe else payloads[a]
            res, misses = run_agent(a, args.depth, items_for_agent, backbones[a], pd, args)
            for r in res:
                r.update(agent=a, depth=args.depth, round=args.round,
                         fold=args.fold, provider=backbones[a].get("provider", ""),
                         model=backbones[a]["model"])
                fh.write(json.dumps(r) + "\n")
            print(f"{a:<15} {len(res):>5} verdicts   {misses:>3} unparsed   "
                  f"model={backbones[a]['model']}")
    print("->", path)


if __name__ == "__main__":
    main()
