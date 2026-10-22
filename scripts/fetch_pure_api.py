#!/usr/bin/env python3
"""Mirror Maastricht's Pure/CRIS ResearchOutput index, which carries DOIs.

The Portal RSS (fetch_pure_publications.py) says WHO wrote what but has no DOI.
This API does have DOIs but cannot filter by person. Joining the two on exact
normalised title gives DOIs without any fuzzy matching, because both sides are
the same Pure record.

Must run on the university network (n3). The Mac and the coder pod cannot reach
crisapi at all. n3 also has http(s)_proxy set, hence ProxyHandler({}).

Needs UBKey, read from .env or $UBKEY. Writes cache/pure_api_titles.json:
  {normalised_title: {"doi": ..., "uuid": ..., "title": ...}}

API quirks, all verified: `format=json` is required (the Accept header is
ignored and XML comes back); paging is `page=N`, 1-based, and `offset` is
silently ignored; an unsupported parameter returns 200 with the FULL unfiltered
result rather than an error.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, unicodedata, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cache" / "pure_api_titles.json"
BASE = "https://crisapi.library.maastrichtuniversity.nl/api/ResearchOutput"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ubkey() -> str:
    if os.environ.get("UBKEY"):
        return os.environ["UBKEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().lower().startswith("ubkey="):
                return line.split("=", 1)[1].strip()
    sys.exit("no UBKey: set $UBKEY or add UBKey= to .env")


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def page(key: str, n: int, size: int, org: str | None) -> dict:
    url = f"{BASE}?format=json&size={size}&page={n}"
    if org:
        url += f"&organisationUuids={org}"
    req = urllib.request.Request(url, headers={"UBKey": key})
    return json.loads(OPENER.open(req, timeout=120).read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=400)
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--org", help="restrict to one organisationUuid")
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()
    key = ubkey()

    first = page(key, 1, 1, args.org)
    total = first.get("count", 0)
    pages = min((total + args.size - 1) // args.size, args.max_pages)
    print(f"{total} research outputs, {pages} pages of {args.size}")

    index: dict[str, dict] = {}
    with_doi = 0
    for n in range(1, pages + 1):
        try:
            d = page(key, n, args.size, args.org)
        except Exception as e:
            print(f"  ! page {n}: {type(e).__name__} {e}", file=sys.stderr)
            continue
        items = d.get("items") or []
        if not items:
            break
        for it in items:
            # Pure splits "Name: Subtitle" across two fields, while the Portal RSS
            # emits them joined. Indexing title alone silently loses every
            # publication with a subtitle, which is most of them.
            t = (it.get("title") or {}).get("value") or ""
            sub = (it.get("subTitle") or {}).get("value") or ""
            full = f"{t}: {sub}" if sub else t
            nt = norm_title(full)
            if not nt:
                continue
            doi = ""
            for ev in it.get("electronicVersions") or []:
                if ev.get("doi"):
                    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", ev["doi"]).lower()
                    break
            if nt in index and not doi:
                continue                      # keep the entry that has a DOI
            index[nt] = {"doi": doi, "uuid": it.get("uuid"), "title": full}
            # also index the bare title, so a stem-only RSS entry still joins
            stem = norm_title(t)
            if sub and stem and stem not in index:
                index[stem] = {"doi": doi, "uuid": it.get("uuid"), "title": full}
            if doi:
                with_doi += 1
        if n % 25 == 0 or n == pages:
            print(f"  page {n}/{pages}  {len(index)} titles  {with_doi} with DOI")
        time.sleep(args.delay)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(index, indent=0, sort_keys=True))
    print(f"\n{len(index)} titles ({with_doi} with DOI) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
