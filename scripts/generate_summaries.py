#!/usr/bin/env python3
"""Generate 2-3 sentence plain-language summaries for publication abstracts.

Reads cache/abstracts/*.txt, calls a local Ollama model for each non-empty
abstract, and caches the summary to cache/summaries/<url-encoded-doi>.txt.
An empty file records a confirmed miss (no abstract available) — skipped.
Pass --refresh to regenerate all summaries.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ABSTRACT_CACHE = ROOT / "cache" / "abstracts"
SUMMARY_CACHE = ROOT / "cache" / "summaries"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:1.5b"

PROMPT = (
    "Write a 2-3 sentence plain-language summary of the following research abstract. "
    "Focus on what was done and why it matters. Do not start with 'This paper' or 'This study'. "
    "Output only the summary, no preamble.\n\nAbstract:\n{abstract}"
)


def safe_filename(doi: str) -> str:
    return urllib.parse.quote(doi, safe="").lower()


def doi_from_filename(name: str) -> str:
    return urllib.parse.unquote(name)


def generate(abstract: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "prompt": PROMPT.format(abstract=abstract),
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.3},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"].strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="regenerate even if cached")
    ap.add_argument("--limit", type=int, default=0, help="process at most N abstracts (0 = all)")
    ap.add_argument("--model", default=MODEL, help=f"Ollama model to use (default: {MODEL})")
    args = ap.parse_args()

    if not ABSTRACT_CACHE.exists():
        print("No abstract cache found. Run fetch_abstracts.py first.")
        return 1

    # verify Ollama is reachable
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
    except Exception:
        print("Ollama not reachable at localhost:11434. Run: ollama serve")
        return 1

    SUMMARY_CACHE.mkdir(parents=True, exist_ok=True)

    abstract_files = sorted(ABSTRACT_CACHE.glob("*.txt"))
    to_process = [f for f in abstract_files if f.read_text().strip()]
    print(f"{len(abstract_files)} abstract cache files, {len(to_process)} non-empty")

    generated = skipped = failed = 0

    for af in to_process:
        if args.limit and generated >= args.limit:
            break

        stem = af.stem
        out = SUMMARY_CACHE / f"{stem}.txt"
        if out.exists() and not args.refresh:
            skipped += 1
            continue

        abstract = af.read_text().strip()
        try:
            summary = generate(abstract, args.model)
            out.write_text(summary)
            generated += 1
            if generated % 25 == 0:
                print(f"  {generated} summaries generated...")
        except Exception as e:
            failed += 1
            print(f"  ! {doi_from_filename(stem)}: {e}")

    print(
        f"\nDone: {generated} generated, {skipped} already cached, {failed} failed\n"
        f"Summaries in {SUMMARY_CACHE.relative_to(ROOT)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
