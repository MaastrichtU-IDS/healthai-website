#!/usr/bin/env python3
"""Fetch abstracts for all DOIs in the Crossref cache.

Sources tried in order (first hit wins):
  1. Crossref   — already cached, ~30% coverage, free
  2. Europe PMC — good for PubMed-indexed biomedical literature
  3. Semantic Scholar — broad CS + biomedical coverage

Each abstract is cached to cache/abstracts/<url-encoded-doi>.txt.
An empty file records a confirmed miss so we don't re-fetch it.
Pass --refresh to force re-fetch all.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CROSSREF_CACHE = ROOT / "cache" / "crossref"
ABSTRACT_CACHE = ROOT / "cache" / "abstracts"
USER_AGENT = "hair-website fetch_abstracts.py (mailto:hair@maastrichtuniversity.nl)"

EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&resulttype=core&format=json"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=abstract"

SLEEP_EPMC = 0.2
SLEEP_S2 = 1.1  # unauthenticated limit is ~1 req/s


def safe_filename(doi: str) -> str:
    return urllib.parse.quote(doi, safe="").lower()


def strip_jats(text: str) -> str:
    """Remove JATS/XML tags Crossref sometimes wraps abstracts in."""
    return re.sub(r"<[^>]+>", " ", text).strip()


def clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(strip_jats(text).split())


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def abstract_from_crossref(doi: str) -> str:
    cf = CROSSREF_CACHE / f"{safe_filename(doi)}.json"
    if not cf.exists():
        return ""
    msg = json.loads(cf.read_text()).get("message") or {}
    return clean(msg.get("abstract", ""))


def abstract_from_epmc(doi: str) -> str:
    try:
        data = fetch_json(EPMC_URL.format(doi=urllib.parse.quote(doi, safe="")))
        results = (data.get("resultList") or {}).get("result") or []
        if results:
            return clean(results[0].get("abstractText", ""))
    except Exception:
        pass
    return ""


def abstract_from_s2(doi: str) -> str:
    try:
        data = fetch_json(S2_URL.format(doi=urllib.parse.quote(doi, safe="")))
        return clean(data.get("abstract", ""))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("  ! Semantic Scholar rate-limited; sleeping 60 s")
            time.sleep(60)
        elif e.code != 404:
            print(f"  ! S2 HTTP {e.code} for {doi}")
    except Exception:
        pass
    return ""


def all_dois() -> list[str]:
    dois = []
    for f in sorted(CROSSREF_CACHE.glob("*.json")):
        try:
            msg = json.loads(f.read_text()).get("message") or {}
            doi = (msg.get("DOI") or "").strip().lower()
            if doi:
                dois.append(doi)
        except Exception:
            pass
    return dois


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--no-s2", action="store_true", help="skip Semantic Scholar (slower)")
    args = ap.parse_args()

    ABSTRACT_CACHE.mkdir(parents=True, exist_ok=True)
    dois = all_dois()
    print(f"{len(dois)} DOIs to process")

    crossref_hit = epmc_hit = s2_hit = miss = skipped = 0

    for doi in dois:
        out = ABSTRACT_CACHE / f"{safe_filename(doi)}.txt"
        if out.exists() and not args.refresh:
            skipped += 1
            continue

        # 1. Crossref (local, no network cost)
        abstract = abstract_from_crossref(doi)
        if abstract:
            out.write_text(abstract)
            crossref_hit += 1
            continue

        # 2. Europe PMC
        abstract = abstract_from_epmc(doi)
        time.sleep(SLEEP_EPMC)
        if abstract:
            out.write_text(abstract)
            epmc_hit += 1
            continue

        # 3. Semantic Scholar
        if not args.no_s2:
            abstract = abstract_from_s2(doi)
            time.sleep(SLEEP_S2)
            if abstract:
                out.write_text(abstract)
                s2_hit += 1
                continue

        # confirmed miss — write empty file so we don't re-fetch
        out.write_text("")
        miss += 1

    total_found = crossref_hit + epmc_hit + s2_hit
    total = len(dois) - skipped
    print(
        f"\nProcessed {total} DOIs ({skipped} already cached)\n"
        f"  Crossref:         {crossref_hit}\n"
        f"  Europe PMC:       {epmc_hit}\n"
        f"  Semantic Scholar: {s2_hit}\n"
        f"  No abstract:      {miss}\n"
        f"  Coverage:         {total_found}/{total} new"
        + (f" + {skipped} pre-cached" if skipped else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
