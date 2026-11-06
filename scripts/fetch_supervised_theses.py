#!/usr/bin/env python3
"""
Find doctoral theses supervised by someone in the group, and report the ones
content/students.yaml does not list yet.

WHY NOT THE PURE API. The CRIS API key's access definition strips the fields
this needs: ResearchOutput comes back without `type`, `personAssociations` or
`publicationStatuses`, so it can say neither that a record is a thesis nor
who supervised it. Issue #23 asked for those fields and was closed, because
the public portal already carries all of it and needs no key at all.

WHY THE `supervised` FEED AND NOT A PERSON'S PUBLICATIONS. A member's own
publication feed lists the thesis they wrote themselves, not the ones they
supervised. Scanning all 36 member feeds finds 6 theses, all of them the
members' own doctorates. The profile's `supervised` tab is the right source
and the only one that answers "whose student was this":

    /en/persons/<slug>/supervised/?format=rss

MASTER'S AND BACHELOR'S THESES ARE NOT HERE. Pure records no master's theses
at all for this group: 450 records across the members' feeds contain 6
theses, every one doctoral. The master's and bachelor's entries in
students.yaml come from Michel's own supervision records and cannot be
checked against Pure. This script is doctoral-only and silent about the rest.

THIS SCRIPT WRITES NOTHING. It reports, and with --yaml prints entries ready
to paste. Two fields it cannot know, both required by students.yaml:

  theme     an id from research.yaml, an editorial judgement
  abstract  absent from most of these records; the ones in students.yaml were
            condensed by hand from the thesis and labelled "Summary" for it

Everything it does print is read off the cited page, which is what
students.yaml requires of any claim in it.

Usage:
    python3 scripts/fetch_supervised_theses.py            # report new ones
    python3 scripts/fetch_supervised_theses.py --all      # every one found
    python3 scripts/fetch_supervised_theses.py --yaml     # paste-ready stubs
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
STUDENTS_YAML = ROOT / "content" / "students.yaml"

PORTAL = "https://cris.maastrichtuniversity.nl"
# The portal answers 403 to urllib's default User-Agent. Identify the site, as
# the other fetchers in this directory do.
USER_AGENT = "healthai-website (+https://github.com/MaastrichtU-IDS/healthai-website)"
PAUSE = 0.4  # between requests, to be a polite guest on someone else's server

# Pure abbreviates some months ("7 Sept 2026"); students.yaml spells them out.
MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April",
          "Jun": "June", "Jul": "July", "Aug": "August", "Sept": "September",
          "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}


def full_month(date: str) -> str:
    return re.sub(r"\b(" + "|".join(MONTHS) + r")\b", lambda m: MONTHS[m.group(1)], date)


def get(url: str, timeout: int = 25) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404 just means this person has no such tab
            print(f"  ! {url} -> HTTP {e.code}", file=sys.stderr)
    except Exception as e:
        print(f"  ! {url} -> {type(e).__name__}: {e}", file=sys.stderr)
    return None


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def slugs_from_people() -> list[tuple[str, str]]:
    """(name, cris slug) for everyone in people.yaml with a CRIS profile."""
    data = yaml.safe_load(PEOPLE_YAML.read_text(encoding="utf-8"))
    people = data if isinstance(data, list) else data.get("people", [])
    out = []
    for p in people:
        url = (p.get("cris_url") or "").strip()
        m = re.search(r"/en/persons/([^/?#]+)", url)
        if m:
            out.append((p.get("name", "?"), m.group(1)))
    return out


def people_names() -> set[str]:
    data = yaml.safe_load(PEOPLE_YAML.read_text(encoding="utf-8"))
    people = data if isinstance(data, list) else data.get("people", [])
    return {p.get("name", "") for p in people}


def supervised_theses(slug: str) -> list[str]:
    """Record URLs of doctoral theses this person supervised."""
    body = get(f"{PORTAL}/en/persons/{slug}/supervised/?format=rss")
    if not body:
        return []
    urls = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        if "Thesis" not in text_of(item):
            continue
        guid = re.search(r"<guid>(.*?)</guid>", item, re.S)
        if guid:
            urls.append(html.unescape(guid.group(1)).strip())
    return urls


def meta(page: str, name: str) -> str | None:
    m = re.search(rf'<meta name="{name}" content="([^"]*)"', page)
    return html.unescape(m.group(1)).strip() if m else None



def parse_supervisors(page: str) -> list[dict]:
    """Supervisors of a thesis, from the "Supervisors/Advisors" row.

    Each one is its own <li>, and an internal UM person is wrapped in
    <span class="person"> while an external one is bare text plus a trailing
    ", External person". The name arrives family-first:

        <li>...<strong class="title"><span class="person">Cavill, Rachel</span>
            </strong>, Co-Supervisor</li>

    Nested spans are why this splits on <li> rather than matching the
    rendering span: a non-greedy match to </span> stops inside the name.
    """
    cell = re.search(r"Supervisors?/Advisors?</th>\s*<td>(.*?)</td>", page, re.S)
    if not cell:
        return []
    out = []
    for li in re.findall(r"<li>(.*?)</li>", cell.group(1), re.S):
        t = text_of(li)
        m = re.match(r"(.+?),\s*([^,]+?)\s*,\s*(Supervisor|Co-Supervisor)\b", t)
        if not m:
            continue
        family, given, role = (x.strip() for x in m.groups())
        out.append({
            "name": f"{given} {family}",
            "role": role,
            "external": "External person" in t or 'class="person"' not in li,
        })
    return out


def parse_record(url: str) -> dict | None:
    """Read a thesis record page. Every field below is visible on that page."""
    page = get(url)
    if not page:
        return None

    date = meta(page, "citation_publication_date") or ""      # 2025/05/22
    year = date.split("/")[0] if date else ""

    supervisors = parse_supervisors(page)

    m = re.search(r"Award date</th>\s*<td>(.*?)</td>", page, re.S)
    human_date = full_month(text_of(m.group(1))) if m else date.replace("/", "-")

    return {
        "title": meta(page, "citation_title") or "",
        "author": meta(page, "citation_author") or "",
        "year": year,
        "date": human_date or date.replace("/", "-"),
        "doi": meta(page, "citation_doi") or "",
        "url": url,
        "supervisors": supervisors,
    }


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def already_listed() -> tuple[set[str], set[str]]:
    """(normalised titles, DOIs) already in students.yaml."""
    data = yaml.safe_load(STUDENTS_YAML.read_text(encoding="utf-8")) or {}
    titles, dois = set(), set()
    for e in data.get("highlights", []) or []:
        titles.add(norm(e.get("title", "")))
        if e.get("doi"):
            dois.add(e["doi"].strip().lower())
    return titles, dois


def q(value: str) -> str:
    """Quote a scalar for YAML. JSON string syntax is valid YAML and handles
    the colons, apostrophes and accents these titles and names are full of;
    an unquoted "Blooming hope: a computational autopsy" is a parse error."""
    return json.dumps(value, ensure_ascii=False)


def as_yaml_stub(rec: dict, known_names: set[str]) -> str:
    sup = "\n".join(f"      - {q(s['name'])}" for s in rec["supervisors"]) or "      # none listed"
    doi = f"\n    doi: {rec['doi']}" if rec["doi"] else ""
    # students.yaml uses the name a person is listed under in people.yaml, not
    # the legal name on the title page, so that it resolves to their profile.
    # Gijs Wijngaard appears on his thesis as Gijsbert Willem Adriaan Wijngaard.
    name_note = "" if rec["author"] in known_names else \
        "  # legal name from the record; use their people.yaml name if they have a profile"
    return f"""  - level: PhD
    name: {q(rec['author'])}{name_note}
    title: {q(rec['title'])}
    year: "{rec['year']}"
    theme: # TODO an id from research.yaml
    date: {q(rec['date'])}
    supervisors:
{sup}{doi}
    url: {rec['url']}
    # TODO abstract, or a Summary condensed from the thesis and labelled as one"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="show every thesis, not just new ones")
    ap.add_argument("--yaml", action="store_true", help="print paste-ready students.yaml entries")
    args = ap.parse_args()

    people = slugs_from_people()
    print(f"Checking the supervised tab of {len(people)} CRIS profiles…", file=sys.stderr)

    urls: dict[str, list[str]] = {}
    for name, slug in people:
        for u in supervised_theses(slug):
            urls.setdefault(u, []).append(name)
        time.sleep(PAUSE)

    print(f"{len(urls)} distinct doctoral theses supervised by the group.", file=sys.stderr)

    titles, dois = already_listed()
    new, known = [], []
    for u, supers in sorted(urls.items()):
        rec = parse_record(u)
        time.sleep(PAUSE)
        if not rec:
            continue
        rec["via"] = supers
        is_known = norm(rec["title"]) in titles or (rec["doi"].lower() in dois if rec["doi"] else False)
        (known if is_known else new).append(rec)

    def show(rec):
        sup = ", ".join(f"{s['name']} ({s['role']})" for s in rec["supervisors"]) or "?"
        print(f"  [{rec['year']}] {rec['title']}")
        print(f"          {rec['author']}  ·  {rec['date']}")
        print(f"          supervisors: {sup}")
        print(f"          {rec['url']}")

    print(f"\n{len(known)} already in students.yaml.")
    if args.all:
        for rec in sorted(known, key=lambda r: r["year"], reverse=True):
            show(rec)

    print(f"\n{len(new)} NOT in students.yaml:")
    for rec in sorted(new, key=lambda r: r["year"], reverse=True):
        show(rec)

    if args.yaml and new:
        print("\n# ---- paste into students.yaml under highlights:, then fill the TODOs ----")
        known_names = people_names()
        for rec in sorted(new, key=lambda r: r["year"], reverse=True):
            print(as_yaml_stub(rec, known_names))
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
