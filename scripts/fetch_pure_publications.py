#!/usr/bin/env python3
"""Fetch each person's publications from the Pure/CRIS API.

Pure is authoritative for Maastricht affiliation, and the API gives per-person
output WITH DOIs in one call. One source, no title matching.

This replaces an earlier Portal-RSS implementation. The portal was used because
black-box probing suggested the API could not filter by person. It can: the
Swagger spec at /swagger/docs/v524 (university network only) documents
`ID_type` on /api/ResearchOutput/{id}. Dropping the portal removed Cloudflare,
a 12s-per-person politeness delay, an exact-title join against a mirror of all
140,126 university records, and a 50-item-per-person cap that was costing real
data: 295 publications for one person via the API against 50 via RSS.

NETWORK: crisapi answers only on the university network. From a cluster pod it
is reached through the egress-proxy, which routes 137.120.0.0/16 direct, so set
HTTPS_PROXY. On n3 no proxy is needed and HTTPS_PROXY should be unset. The code
honours the environment rather than hardcoding either.

Needs UBKey from $UBKEY or .env. Writes cache/pure/<slug>.json.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
PEOPLE = ROOT / "content" / "people.yaml"
CACHE = ROOT / "cache" / "pure"
BASE = "https://crisapi.library.maastrichtuniversity.nl/api"
PAGE = 200


def ubkey() -> str:
    if os.environ.get("UBKEY"):
        return os.environ["UBKEY"].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().lower().startswith("ubkey="):
                return line.split("=", 1)[1].strip()
    sys.exit("no UBKey: set $UBKEY or add UBKey= to .env")


# PROXY IS OPT-IN, and must be, because inheriting the environment gets it
# wrong on n3. n3's shell sets HTTPS_PROXY to the general university proxy for
# internet egress, and that proxy cannot tunnel to crisapi -- it answers
# "503 Service Unavailable". crisapi is on 137.120.0.0/16 and n3 reaches it
# directly, so on n3 the right behaviour is NO proxy at all.
#
# In a cluster pod the opposite holds: there is no direct route out, and the
# egress-proxy (which routes 137.120.0.0/16 direct, per services#597) must be
# used. So set PURE_HTTPS_PROXY there, e.g.
#   PURE_HTTPS_PROXY=http://egress-proxy.platform.svc.cluster.local:3128
# and leave it unset everywhere else. Deliberately a dedicated variable rather
# than HTTPS_PROXY, so an ambient proxy setting cannot silently break this.
_proxy = os.environ.get("PURE_HTTPS_PROXY", "").strip()
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"https": _proxy, "http": _proxy} if _proxy else {})
)


class NotFound(Exception):
    """Pure found no match.

    Not an HTTP 404: the API answers 200 with an XML body,
    `<XML>Nothing found / 404</XML>`, even when asked for JSON. Without a
    type of its own this arrives as a JSONDecodeError and is indistinguishable
    from the genuinely malformed responses the service also emits.
    """


def api(key: str, path: str, _tries: int = 2, **params) -> dict:
    """One GET. Retries once on a malformed body: the service intermittently
    returns non-JSON (seen mid-run on 2026-10-22), and a single bad response
    should not end the whole job."""
    url = f"{BASE}/{path}?" + urllib.parse.urlencode({"format": "json", **params})
    last = None
    for attempt in range(_tries):
        try:
            req = urllib.request.Request(url, headers={"UBKey": key})
            body = _opener.open(req, timeout=120).read()
            # Check before parsing: "nothing found" is a settled answer, so
            # retrying it just costs the 3s sleep below, once per person.
            if body.lstrip()[:1] == b"<":
                if b"Nothing found" in body:
                    raise NotFound(path)
                raise ValueError(f"unexpected XML body from {path}: {body[:120]!r}")
            return json.loads(body)
        except json.JSONDecodeError as e:
            last = e
            if attempt + 1 < _tries:
                time.sleep(3)
    raise last


def person_uuid(key: str, person: dict) -> str | None:
    """Resolve a person to their Pure uuid: cris_url, then ORCID, then name.

    Returns None ONLY when Pure answered and had no match. Anything that
    stopped the question being asked -- a revoked UBKey (401), a 5xx, a dead
    proxy -- is raised. The two handlers here used to swallow those, and a
    silent None is indistinguishable from "this person has no Pure record":
    the caller counted them as absent, kept last run's cache, and exited 0.
    """
    m = re.search(r"/persons/([0-9a-f]{8}-[0-9a-f-]{27})", person.get("cris_url", "") or "")
    if m:
        return m.group(1)
    if person.get("orcid"):
        try:
            r = api(key, f"Person/{person['orcid']}", ID_type="orcid")
            # NOTE: this endpoint returns a BARE object, not {"items": [...]}.
            if isinstance(r, dict) and r.get("uuid"):
                return r["uuid"]
        except NotFound:
            pass  # no person with that ORCID; fall through and try by name
        except urllib.error.HTTPError as e:
            # 404 is the honest answer "no person with that ORCID"; fall
            # through and try by name. Anything else is our problem.
            if e.code != 404:
                raise
    try:
        r = api(key, "Person", searchString=person.get("name", ""), size=5)
        items = r.get("items") or []
        if len(items) == 1:
            return items[0].get("uuid")
    except NotFound:
        pass
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    return None


def outputs(key: str, uuid: str) -> list[dict]:
    """All research outputs for a person, paged. `page` is 1-based."""
    got, page = [], 1
    while True:
        try:
            d = api(key, f"ResearchOutput/{uuid}", ID_type="person_uuids", size=PAGE, page=page)
        except NotFound:
            break  # no outputs recorded for this person
        items = d.get("items") or []
        if not items:
            break
        got.extend(items)
        if len(got) >= (d.get("count") or 0) or len(items) < PAGE:
            break
        page += 1
    return got


def simplify(it: dict) -> dict:
    t = (it.get("title") or {}).get("value") or ""
    sub = (it.get("subTitle") or {}).get("value") or ""
    doi = ""
    for ev in it.get("electronicVersions") or []:
        if ev.get("doi"):
            doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", ev["doi"]).lower()
            break
    return {
        "uuid": it.get("uuid"),
        "title": f"{t}: {sub}" if sub else t,
        "doi": doi,
        "portal_url": (it.get("info") or {}).get("portalUrl", ""),
        "type": ((it.get("type") or {}).get("term") or {}).get("value", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="only people whose name contains this")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()
    key = ubkey()

    people = yaml.safe_load(PEOPLE.read_text()) or []
    targets = [p for p in people if p.get("cris_url") or p.get("orcid") or p.get("name")]
    if args.name:
        targets = [p for p in targets if args.name.lower() in (p.get("name") or "").lower()]
    if args.limit:
        targets = targets[: args.limit]

    CACHE.mkdir(parents=True, exist_ok=True)

    def slug_for(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    # A cache file for someone no longer in people.yaml would keep publishing
    # their work: build_publications.py globs this directory rather than
    # reading people.yaml, so a departed member never leaves the site. Only
    # prune on a full run, since a filtered one does not know the full set.
    if not args.name and not args.limit:
        current = {slug_for(p.get("name") or "") for p in targets}
        for stale in sorted(CACHE.glob("*.json")):
            if stale.stem not in current:
                stale.unlink()
                print(f"      {stale.stem[:32]:<34} no longer in people.yaml, cache removed")

    total = withdoi = no_record = errored = 0
    print(f"{len(targets)} people")
    for i, p in enumerate(targets, 1):
        name = p.get("name", "?")
        try:
            uuid = person_uuid(key, p)
        except Exception as e:
            print(f"  {i:>2}. {name[:32]:<34} lookup failed: {type(e).__name__}", file=sys.stderr)
            errored += 1
            continue
        if not uuid:
            no_record += 1
            print(f"  {i:>2}. {name[:32]:<34} no Pure person record")
            continue
        try:
            items = [simplify(x) for x in outputs(key, uuid)]
        except Exception as e:
            print(f"  {i:>2}. {name[:32]:<34} ERROR {type(e).__name__}: {e}", file=sys.stderr)
            errored += 1
            continue
        d = sum(1 for x in items if x["doi"])
        total += len(items)
        withdoi += d
        (CACHE / f"{slug_for(name)}.json").write_text(json.dumps(
            {"name": name, "pure_uuid": uuid, "count": len(items), "items": items}, indent=1))
        print(f"  {i:>2}. {name[:32]:<34} {len(items):>4} outputs, {d:>4} with DOI")
        time.sleep(args.delay)
    print(f"\n{total} outputs, {withdoi} with DOI, "
          f"{no_record} with no Pure record, {errored} errored -> {CACHE}")

    # A failed person is not a missing person: their cache file from the last
    # good run survives untouched, so the build reproduces it and the job
    # reports "no change" and exits green. A revoked UBKEY fails all 38 people
    # exactly that way, and the shrink guard cannot see it because it compares
    # against the cache, not against the fetch. So an error here has to be the
    # thing that stops the pipeline.
    if errored:
        print(f"ABORT: {errored} people errored; their cached data would be "
              f"republished unchanged and the run would look clean.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
