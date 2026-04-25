#!/usr/bin/env python3
"""Fetch each researcher's ORCID works summary.

Reads ORCID IDs from content/people.yaml, hits the ORCID public API, and
caches raw JSON to cache/orcid/<orcid>.json. Skips fetch when cached unless
--refresh is passed.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
CACHE_DIR = ROOT / "cache" / "orcid"
USER_AGENT = "hair-website fetch_orcid_works.py (mailto:hair@maastrichtuniversity.nl)"
ENDPOINT = "https://pub.orcid.org/v3.0/{orcid}/works"
SLEEP = 0.2


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
    ap.add_argument("--name", help="only fetch people whose name contains this (case-insensitive)")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    people = yaml.safe_load(PEOPLE_YAML.read_text())
    if args.name:
        needle = args.name.lower()
        people = [p for p in people if needle in (p.get("name") or "").lower()]
        print(f"Filtered to {len(people)} person(s) matching '{args.name}'.")

    fetched = skipped = failed = 0
    for p in people:
        orcid = (p.get("orcid") or "").strip()
        name = p.get("name", "?")
        if not orcid:
            print(f"  - {name}: no orcid, skipping")
            continue
        out = CACHE_DIR / f"{orcid}.json"
        if out.exists() and not args.refresh:
            skipped += 1
            print(f"  = {name}: cached")
            continue
        try:
            data = fetch(ENDPOINT.format(orcid=orcid))
            out.write_bytes(data)
            fetched += 1
            print(f"  + {name}: fetched")
            time.sleep(SLEEP)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  ! {name} ({orcid}): HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ! {name} ({orcid}): {e}")

    print(f"\n{fetched} fetched, {skipped} cached, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
