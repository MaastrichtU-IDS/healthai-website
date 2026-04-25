#!/usr/bin/env python3
"""Build content/publications.generated.yaml from ORCID + Crossref caches.

Combines DOIs claimed by each researcher (ORCID) with full metadata (Crossref),
dedupes by DOI, sorts by year desc, and emits the existing publications.yaml
schema. Computes stats: total, citations, h_index, this_year.

Output goes to publications.generated.yaml so the curated publications.yaml
is preserved until you're satisfied with the result.
"""
from __future__ import annotations

import datetime
import json
import sys
import urllib.parse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ORCID_CACHE = ROOT / "cache" / "orcid"
CROSSREF_CACHE = ROOT / "cache" / "crossref"
ABSTRACT_CACHE = ROOT / "cache" / "abstracts"
SUMMARY_CACHE = ROOT / "cache" / "summaries"
OUT_YAML = ROOT / "content" / "publications.generated.yaml"

TYPE_MAP = {
    "journal-article": "Journal",
    "proceedings-article": "Conference",
    "book-chapter": "Book Chapter",
    "book": "Book",
    "monograph": "Book",
    "edited-book": "Book",
    "posted-content": "Preprint",
    "report": "Report",
    "dataset": "Dataset",
    "dissertation": "Thesis",
}

EXCLUDE_DOIS = {
    "10.2307/415352",  # misattributed 1989 linguistics book review (Brewster)
}


def safe_filename(doi: str) -> str:
    return urllib.parse.quote(doi, safe="").lower()


def load_abstract(doi: str) -> str:
    f = ABSTRACT_CACHE / f"{safe_filename(doi)}.txt"
    return f.read_text().strip() if f.exists() else ""


def load_summary(doi: str) -> str:
    f = SUMMARY_CACHE / f"{safe_filename(doi)}.txt"
    return f.read_text().strip() if f.exists() else ""


def normalize_doi(d: str) -> str:
    return (
        (d or "")
        .strip()
        .lower()
        .replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
    )


def map_type(t: str | None) -> str:
    return TYPE_MAP.get((t or "").lower(), (t or "Other").replace("-", " ").title())


def issued_year(message: dict) -> int | None:
    for key in ("issued", "published-print", "published-online", "created"):
        parts = ((message.get(key) or {}).get("date-parts") or [[]])[0]
        if parts:
            return parts[0]
    return None


def author_str(authors: list | None) -> str:
    parts = []
    for a in authors or []:
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if not family:
            continue
        initials = " ".join(p[0] + "." for p in given.split() if p and p[0].isalpha())
        parts.append(f"{family} {initials}".strip() if initials else family)
    return ", ".join(parts)


def venue(message: dict) -> str:
    titles = message.get("container-title") or []
    return titles[0] if titles else ""


def doi_to_orcids() -> dict[str, set[str]]:
    """Map normalized DOI -> set of ORCID ids that claim it."""
    mapping: dict[str, set[str]] = {}
    for f in ORCID_CACHE.glob("*.json"):
        data = json.loads(f.read_text())
        for grp in data.get("group") or []:
            for ws in grp.get("work-summary") or []:
                for ext in (ws.get("external-ids") or {}).get("external-id") or []:
                    if (ext.get("external-id-type") or "").lower() == "doi":
                        d = normalize_doi(ext.get("external-id-value") or "")
                        if d:
                            mapping.setdefault(d, set()).add(f.stem)
    return mapping


def h_index(citations: list[int]) -> int:
    h = 0
    for i, c in enumerate(sorted(citations, reverse=True), 1):
        if c >= i:
            h = i
        else:
            break
    return h


def main() -> int:
    if not ORCID_CACHE.exists() or not CROSSREF_CACHE.exists():
        print("Caches missing. Run fetch_orcid_works.py and fetch_crossref.py first.")
        return 1

    claims = doi_to_orcids()
    print(f"{len(claims)} DOIs from ORCID")

    pubs = []
    citations: list[int] = []
    citations_total = 0
    missing = 0

    for doi in sorted(claims):
        if doi in EXCLUDE_DOIS:
            continue
        cf = CROSSREF_CACHE / f"{safe_filename(doi)}.json"
        if not cf.exists():
            missing += 1
            continue
        msg = (json.loads(cf.read_text()).get("message") or {})
        year = issued_year(msg)
        title_arr = msg.get("title") or []
        title = title_arr[0].strip() if title_arr else ""
        if not year or not title:
            continue
        cites = int(msg.get("is-referenced-by-count") or 0)
        citations_total += cites
        citations.append(cites)
        entry: dict = {
            "year": year,
            "type": map_type(msg.get("type")),
            "title": title,
            "authors": author_str(msg.get("author")),
            "venue": venue(msg),
            "doi": doi,
            "citations": cites,
            "orcids": sorted(claims[doi]),
            "tags": [],
        }
        abstract = load_abstract(doi)
        if abstract:
            entry["abstract"] = abstract
        summary = load_summary(doi)
        if summary:
            entry["summary"] = summary
        pubs.append(entry)

    pubs.sort(key=lambda p: (-p["year"], p["title"].lower()))

    current_year = datetime.date.today().year
    this_year = sum(1 for p in pubs if p["year"] == current_year)

    out = {
        "stats": {
            "total": len(pubs),
            "citations": citations_total,
            "h_index": h_index(citations),
            "this_year": this_year,
            "this_year_label": f"Published in {current_year}",
        },
        "pure_notice": (
            "Auto-generated from ORCID + Crossref. "
            "For the live record visit our PURE research portal."
        ),
        "publications": pubs,
    }

    OUT_YAML.write_text(
        "# Auto-generated by scripts/build_publications.py — do not edit directly.\n"
        + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=10000)
    )
    print(
        f"Wrote {len(pubs)} publications "
        f"({missing} DOIs missing crossref metadata) "
        f"-> {OUT_YAML.relative_to(ROOT)}"
    )
    print(
        f"  citations: {citations_total:,}  "
        f"h-index: {h_index(citations)}  "
        f"{current_year}: {this_year}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
