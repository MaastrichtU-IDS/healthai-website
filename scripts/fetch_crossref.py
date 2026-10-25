#!/usr/bin/env python3
"""Enrich publications by DOI via Crossref.

Reads cache/orcid/*.json, extracts every DOI, fetches Crossref metadata for
each unique DOI, and caches to cache/crossref/<url-encoded-doi>.json.

A cache entry older than --max-age-days is re-fetched. Citation counts live
in this cache (is-referenced-by-count, which build_publications.py turns into
the site's citation total and h-index), so a cache that is never re-read
freezes those numbers at whenever each DOI was first seen while the weekly
job keeps committing them as a refresh.
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
USER_AGENT = "healthai-website fetch_crossref.py (mailto:hair@maastrichtuniversity.nl)"
ENDPOINT = "https://api.crossref.org/works/{doi}"
SLEEP = 0.05
# A month. Citation counts move slowly, and this is some 650 DOIs: re-fetching
# the lot every week would be ~650 requests a week to keep numbers that barely
# change. Entries expire on the anniversary of their own fetch, so the load
# spreads itself across weeks rather than arriving all at once.
MAX_AGE_DAYS = 30


def is_fresh(path: Path, max_age_days: float) -> bool:
    if not path.exists():
        return False
    if max_age_days <= 0:
        return False
    return (time.time() - path.stat().st_mtime) < max_age_days * 86400


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
    ap.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS,
                    help="re-fetch a cache entry older than this (0 = always)")
    args = ap.parse_args()

    if not ORCID_CACHE.exists():
        print(f"No ORCID cache at {ORCID_CACHE}. Run fetch_orcid_works.py first.")
        return 1

    CROSSREF_CACHE.mkdir(parents=True, exist_ok=True)

    all_dois: set[str] = set()
    for f in sorted(ORCID_CACHE.glob("*.json")):
        all_dois.update(extract_dois(json.loads(f.read_text())))
    print(f"{len(all_dois)} unique DOIs across ORCID caches.")

    # Pure contributes DOIs that ORCID does not have, and without this they
    # reach build_publications.py with no Crossref metadata and are silently
    # dropped from the output.
    pure_dir = ROOT / "cache" / "pure"
    if pure_dir.exists():
        before = len(all_dois)
        for f in sorted(pure_dir.glob("*.json")):
            for item in json.loads(f.read_text()).get("items", []):
                if item.get("doi"):
                    all_dois.add(item["doi"])
        print(f"{len(all_dois) - before} further DOIs from the Pure cache "
              f"({len(all_dois)} total).")

    fetched = skipped = failed = missing = 0
    for doi in sorted(all_dois):
        out = CROSSREF_CACHE / f"{safe_filename(doi)}.json"
        if is_fresh(out, args.max_age_days) and not args.refresh:
            skipped += 1
            continue
        url = ENDPOINT.format(doi=urllib.parse.quote(doi, safe="/"))
        try:
            out.write_bytes(fetch(url))
            fetched += 1
            time.sleep(SLEEP)
        except urllib.error.HTTPError as e:
            # A 404 means Crossref does not have this DOI, which is permanent
            # and expected: arXiv and Zenodo DOIs are registered with DataCite,
            # and some legacy DOIs are simply malformed. Counting those as
            # failures made this script exit non-zero on every run, and under
            # `set -e` in refresh_publications.sh that aborted the whole
            # pipeline before the build. Seen in the cluster on 2026-10-23:
            # "646 fetched, 0 cached, 28 failed", exit 1, nothing published.
            if e.code == 404:
                missing += 1
                print(f"  - {doi}: not in Crossref (HTTP 404)")
                # Record the absence so the expiry above does not re-ask
                # Crossref about the same arXiv and Zenodo DOIs every month.
                # build_publications.py skips an entry with no "message".
                out.write_text(json.dumps({"status": "not-found", "doi": doi}))
            else:
                failed += 1
                print(f"  ! {doi}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            # Anything that is not an HTTP status -- DNS, TLS, timeouts, a
            # missing proxy -- is a real problem and must stop the pipeline
            # rather than quietly yield a thinner publication list.
            failed += 1
            print(f"  ! {doi}: {e}")

    print(f"\n{fetched} fetched, {skipped} cached, "
          f"{missing} not in Crossref, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
