#!/usr/bin/env python3
"""Enrich publications by DOI via Crossref.

Reads cache/orcid/*.json, extracts every DOI, fetches Crossref metadata for
each unique DOI, and caches to cache/crossref/<url-encoded-doi>.json.
Skips fetch when cached unless --refresh is passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCID_CACHE = ROOT / "cache" / "orcid"
CROSSREF_CACHE = ROOT / "cache" / "crossref"
USER_AGENT = "hair-website fetch_crossref.py (mailto:hair@maastrichtuniversity.nl)"
ENDPOINT = "https://api.crossref.org/works/{doi}"
SLEEP = 0.05


def safe_filename(doi: str) -> str:
    return urllib.parse.quote(doi, safe="").lower()


def normalize_doi(d: str) -> str:
    return (
        (d or "")
        .strip()
        .lower()
        .replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
    )


def extract_dois(orcid_json: dict) -> set[str]:
    dois: set[str] = set()
    for grp in orcid_json.get("group") or []:
        for ws in grp.get("work-summary") or []:
            for ext in (ws.get("external-ids") or {}).get("external-id") or []:
                if (ext.get("external-id-type") or "").lower() == "doi":
                    val = normalize_doi(ext.get("external-id-value") or "")
                    if val:
                        dois.add(val)
    return dois


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    args = ap.parse_args()

    if not ORCID_CACHE.exists():
        print(f"No ORCID cache at {ORCID_CACHE}. Run fetch_orcid_works.py first.")
        return 1

    CROSSREF_CACHE.mkdir(parents=True, exist_ok=True)

    all_dois: set[str] = set()
    for f in sorted(ORCID_CACHE.glob("*.json")):
        all_dois.update(extract_dois(json.loads(f.read_text())))
    print(f"{len(all_dois)} unique DOIs across ORCID caches.")

    fetched = skipped = failed = 0
    for doi in sorted(all_dois):
        out = CROSSREF_CACHE / f"{safe_filename(doi)}.json"
        if out.exists() and not args.refresh:
            skipped += 1
            continue
        url = ENDPOINT.format(doi=urllib.parse.quote(doi, safe="/"))
        try:
            out.write_bytes(fetch(url))
            fetched += 1
            time.sleep(SLEEP)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  ! {doi}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ! {doi}: {e}")

    print(f"\n{fetched} fetched, {skipped} cached, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
