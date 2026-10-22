#!/usr/bin/env python3
"""Join Pure Portal titles against the Pure API index to get DOIs exactly.

Both sides are the same Pure record, so titles agree exactly and no similarity
scoring is involved. Anything this cannot resolve falls through to
resolve_pure_dois.py, which queries Crossref and does have to guess.

Note Pure splits "Name: Subtitle" across title and subTitle in the API but joins
them in the Portal RSS. fetch_pure_api.py indexes both forms; without that, every
subtitled publication silently fails to join (it was 80% match, now 99%).
"""
from __future__ import annotations
import json, re, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PURE, API = ROOT / "cache" / "pure", ROOT / "cache" / "pure_api_titles.json"
OUT = ROOT / "cache" / "pure_dois_exact.json"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def main() -> int:
    if not API.exists():
        print(f"no {API} -- run fetch_pure_api.py on the university network", file=sys.stderr)
        return 1
    api = json.loads(API.read_text())
    rss = {}
    for f in sorted(PURE.glob("*.json")):
        for it in json.loads(f.read_text()).get("items", []):
            rss.setdefault(norm(it["title"]), it["title"])
    hit = {n: api[n] for n in rss if n in api}
    doi = {n: v for n, v in hit.items() if v.get("doi")}
    OUT.write_text(json.dumps(
        {n: {"doi": v["doi"], "uuid": v.get("uuid"), "via": "pure-api-exact",
             "matched_title": rss[n], "score": 1.0} for n, v in doi.items()},
        indent=1, sort_keys=True))
    print(f"{len(rss)} portal titles, {len(hit)} joined ({100*len(hit)//max(len(rss),1)}%), "
          f"{len(doi)} with DOI -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
