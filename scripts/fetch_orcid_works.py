#!/usr/bin/env python3
"""Fetch each researcher's ORCID works summary.

Reads ORCID IDs from content/people.yaml, hits the ORCID public API, and
caches raw JSON to cache/orcid/<orcid>.json.

A cache entry older than --max-age-days is re-fetched. Without that the
weekly job skipped every ORCID it had ever seen, so a claim a person added
after their first fetch never appeared, and "refresh" refreshed nothing.
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
# A week, so the weekly job re-fetches everything. 38 requests is nothing;
# the cache is here to survive a failed run, not to spare ORCID the traffic.
MAX_AGE_DAYS = 7


def is_fresh(path: Path, max_age_days: float) -> bool:
    if not path.exists():
        return False
    if max_age_days <= 0:
        return False
    return (time.time() - path.stat().st_mtime) < max_age_days * 86400


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
    ap.add_argument("--name", help="only fetch people whose name contains this (case-insensitive)")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    people = yaml.safe_load(PEOPLE_YAML.read_text())
    if args.name:
        needle = args.name.lower()
        people = [p for p in people if needle in (p.get("name") or "").lower()]
        print(f"Filtered to {len(people)} person(s) matching '{args.name}'.")

    # A cache file for someone no longer in people.yaml would keep publishing
    # their work: build_publications.py globs this directory rather than
    # reading people.yaml, so a departed member never leaves the site. Only
    # prune on a full run, since a filtered one does not know the full set.
    if not args.name:
        current = {(p.get("orcid") or "").strip() for p in people}
        for stale in sorted(CACHE_DIR.glob("*.json")):
            if stale.stem not in current:
                stale.unlink()
                print(f"  x {stale.stem}: no longer in people.yaml, cache removed")

    fetched = skipped = failed = missing = 0
    for p in people:
        orcid = (p.get("orcid") or "").strip()
        name = p.get("name", "?")
        if not orcid:
            print(f"  - {name}: no orcid, skipping")
            continue
        out = CACHE_DIR / f"{orcid}.json"
        if is_fresh(out, args.max_age_days) and not args.refresh:
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
            # 404 (no such record) and 409 (deactivated or deprecated) are
            # permanent facts about that ORCID, not failures of this run.
            # Counting them as failures made the script exit 1 on every run,
            # and `set -e` in refresh_publications.sh then aborted the whole
            # pipeline before the build -- the same trap fixed in
            # fetch_crossref.py in 63cbff5.
            if e.code in (404, 409):
                missing += 1
                print(f"  - {name} ({orcid}): no ORCID record (HTTP {e.code})")
            else:
                failed += 1
                print(f"  ! {name} ({orcid}): HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ! {name} ({orcid}): {e}")

    print(f"\n{fetched} fetched, {skipped} cached, "
          f"{missing} with no ORCID record, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
