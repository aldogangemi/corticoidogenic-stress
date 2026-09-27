#!/usr/bin/env python3
"""
verify_models.py — check every id in models.json against the provider's live
model list, before you spend 33k calls discovering a 404 on fold 7.

    export ANTHROPIC_API_KEY=... OPENAI_API_KEY=... GEMINI_API_KEY=...
    python3 verify_models.py --models models.json

Model ids churn fast and the ones shipped in models.json are a best effort from
public documentation, not a guarantee. This queries the authoritative endpoint
for each provider and prints exact matches, near matches, and misses.

  anthropic  GET https://api.anthropic.com/v1/models
  openai     GET https://api.openai.com/v1/models
  google     GET https://generativelanguage.googleapis.com/v1beta/models
"""
from __future__ import annotations
import argparse, json, os, ssl, sys, urllib.request, difflib


def ssl_ctx():
    """macOS/framework Python often ships without a usable CA store, which
    surfaces as CERTIFICATE_VERIFY_FAILED on every https call from urllib.
    certifi provides the bundle; SSL_CERT_FILE overrides it if set."""
    try:
        import certifi
        return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE")
                                          or certifi.where())
    except ImportError:
        return ssl.create_default_context()

ENDPOINTS = {
    "anthropic": ("https://api.anthropic.com/v1/models",
                  lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"},
                  "ANTHROPIC_API_KEY", lambda j: [m["id"] for m in j.get("data", [])]),
    "openai": ("https://api.openai.com/v1/models",
               lambda k: {"Authorization": f"Bearer {k}"},
               "OPENAI_API_KEY", lambda j: [m["id"] for m in j.get("data", [])]),
    "google": ("https://generativelanguage.googleapis.com/v1beta/models",
               lambda k: {"x-goog-api-key": k},
               "GEMINI_API_KEY",
               lambda j: [m["name"].split("/")[-1] for m in j.get("models", [])]),
}


def fetch(provider):
    url, hdr, envvar, parse = ENDPOINTS[provider]
    key = os.environ.get(envvar)
    if not key:
        return None, f"{envvar} not set"
    req = urllib.request.Request(url, headers=hdr(key))
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as r:
            return parse(json.load(r)), None
    except Exception as e:
        return None, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models.json")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="continue when ids could not be checked (network/TLS/no key)")
    ap.add_argument("--fix", action="store_true",
                    help="rewrite models.json with the closest available id")
    a = ap.parse_args()

    models = json.load(open(a.models))
    cache, ok, changed, skipped = {}, True, False, 0
    for m in models:
        p = m["provider"]
        if p not in cache:
            cache[p] = fetch(p)
        avail, err = cache[p]
        if avail is None:
            print(f"  {m['model']:<26} {p:<10} SKIP  ({err})")
            skipped += 1
            continue
        if m["model"] in avail:
            print(f"  {m['model']:<26} {p:<10} OK")
            continue
        # prefer ids that EXTEND the requested one (foo -> foo-preview, foo-latest,
        # foo-20260101) over lexically similar but semantically different models.
        # Without this, "gemini-3.1-pro" scores closer to "gemini-2.5-pro" than to
        # "gemini-3.1-pro-preview" and silently downgrades a generation.
        ext = sorted((x for x in avail if x.startswith(m["model"])), key=len)
        near = ext + [x for x in difflib.get_close_matches(m["model"], avail, n=3,
                                                           cutoff=0.4) if x not in ext]
        print(f"  {m['model']:<26} {p:<10} NOT FOUND")
        if near:
            print(f"      closest available: {', '.join(near)}")
            if a.fix:
                taken = {x["model"] for x in models if x is not m}
                pick = next((x for x in near if x not in taken), None)
                if pick is None:
                    print("      -> NOT substituting: every close match is already "
                          "used by another role. Two roles on one model would break "
                          "the panel's independence assumption. Fix this id by hand.")
                    ok = False
                else:
                    if pick is not near[0]:
                        print(f"      (skipped {near[0]}: already assigned to another role)")
                    m["model"] = pick
                    changed = True
                    print(f"      -> using {pick}")
        else:
            print(f"      first ids from this provider: {', '.join(sorted(avail)[:6])}")
        ok = ok and a.fix

    if a.fix and changed:
        json.dump(models, open(a.models, "w"), indent=1)
        print(f"\nrewrote {a.models}")
    if skipped:
        print(f"\n{skipped} id(s) could not be checked (network/TLS/no key).")
        print("If this is CERTIFICATE_VERIFY_FAILED:  pip install certifi")
        print("  and, on macOS, run:  /Applications/Python*/Install\\ Certificates.command")
        print("The provider SDKs used by run_panel.py carry their own CA bundle, so the "
              "run itself may work regardless. Re-run with --allow-unverified to proceed.")
        if not a.allow_unverified:
            sys.exit(1)
    if not ok:
        sys.exit("\nfix the ids above (or re-run with --fix) before run_chain.py")
    print("\nall ids verified." if not skipped else "\nproceeding without verification.")


if __name__ == "__main__":
    main()
