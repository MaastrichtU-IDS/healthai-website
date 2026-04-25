#!/usr/bin/env python3
"""Append new people.yaml entries by scraping UM staff pages + CRIS + Scholar.

Reuses the fetchers / extractors from ``enrich_people.py`` for the UM page
(image, bio) and adds:
  - email + ORCID extraction from the UM page HTML
  - CRIS URL discovery via CRIS ``searchAll`` endpoint
  - Google Scholar author ID discovery via the ``citations`` author search

Per project convention, ``google_scholar`` stores the bare ``user=<id>`` value;
``cris_url`` and ``work_url`` store full URLs. All fields are best-effort —
missing values are left blank for the user to fill in. All HTTP responses are
cached under ``cache/`` so reruns are cheap.

Usage:
  python scripts/add_people.py "Full Name" https://www.maastrichtuniversity.nl/slug
  python scripts/add_people.py "Name 1" https://...url1 "Name 2" https://...url2
  # or edit NEW_PEOPLE below and run without arguments
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich_people import (  # noqa: E402
    BIO_WIDTH,
    download_image,
    extract_bio,
    extract_profile_image,
    load_page,
)

ROOT = Path(__file__).resolve().parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
SEARCH_CACHE_DIR = ROOT / "cache" / "search_pages"

# CRIS and Scholar both reject the minimal UA used by enrich_people.py (HTTP
# 403). A realistic browser UA works for a handful of one-off lookups.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def browser_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

NEW_PEOPLE: list[tuple[str, str]] = [
    # ("Full Name", "https://www.maastrichtuniversity.nl/um-slug"),
]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def load_search(label: str, url: str) -> str | None:
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = SEARCH_CACHE_DIR / f"{label}.html"
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8")
    try:
        body = browser_fetch(url)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"    search fetch failed ({label}): {e}", file=sys.stderr)
        return None
    path.write_text(body, encoding="utf-8")
    return body


EMAIL_DOMAIN_PREF = "@maastrichtuniversity.nl"
# UM chrome/footer addresses that aren't the person's contact email.
EMAIL_BLOCKLIST = {
    "webred-um@maastrichtuniversity.nl",
    "info@maastrichtuniversity.nl",
    "press@maastrichtuniversity.nl",
}


def _accept_email(addr: str) -> bool:
    return addr.lower() not in EMAIL_BLOCKLIST


def extract_email(html_text: str) -> str | None:
    for m in re.finditer(r'mailto:([^"\'\s<>]+@[^"\'\s<>]+)', html_text):
        addr = html.unescape(m.group(1)).split("?", 1)[0]
        if EMAIL_DOMAIN_PREF in addr and _accept_email(addr):
            return addr
    for m in re.finditer(r"\b[\w.+-]+@maastrichtuniversity\.nl\b", html_text):
        if _accept_email(m.group(0)):
            return m.group(0)
    return None


ORCID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")


def extract_orcid(html_text: str) -> str | None:
    m = re.search(r"orcid\.org/" + ORCID_RE.pattern, html_text)
    if m:
        return m.group(1)
    m = ORCID_RE.search(html_text)
    if m:
        return m.group(1)
    return None


def _head(url: str) -> int:
    """Return the HTTP status of a HEAD request, or 0 on network failure."""
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": BROWSER_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError):
        return 0


def find_cris_url(name: str) -> str | None:
    """Try the conventional CRIS slug and confirm via HEAD.

    CRIS's ``searchAll`` endpoint is behind a Cloudflare JS challenge and
    unreachable without a real browser, but individual ``/en/persons/<slug>/``
    pages are served directly. CRIS slugs on every existing entry follow
    ``firstname-lastname`` (lowercase, spaces → hyphens).
    """
    slug = re.sub(r"-+", "-", name.lower().replace(" ", "-"))
    url = f"https://cris.maastrichtuniversity.nl/en/persons/{urllib.parse.quote(slug)}/"
    code = _head(url)
    if code == 200:
        return url
    return None


SCHOLAR_LINK_RE = re.compile(
    r'<a[^>]*href="/citations\?user=([A-Za-z0-9_-]{10,14})[^"]*"[^>]*>(.+?)</a>',
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")


def find_scholar_id(name: str) -> str | None:
    """Find a Scholar user= ID by scraping co-author links in article results.

    Scholar's ``view_op=search_authors`` endpoint now redirects to Google
    account login, but the plain ``/scholar?q=...`` article search still
    returns public HTML. Each article byline contains ``<a href="/citations
    ?user=ID">Author</a>`` links for every co-author with a Scholar profile.
    We pick the first link whose visible name matches our query by surname
    and leading initial (Scholar abbreviates first names to initials in
    article bylines, e.g. ``F Arruda Pontes``).
    """
    q = urllib.parse.quote(f"{name} Maastricht")
    url = f"https://scholar.google.com/scholar?hl=en&q={q}"
    body = load_search(f"scholar__{slugify(name)}", url)
    if not body:
        return None
    tokens = name.split()
    if not tokens:
        return None
    surname = tokens[-1].lower()
    first_initial = tokens[0][0].lower()
    for uid, link_text in SCHOLAR_LINK_RE.findall(body):
        plain = html.unescape(TAG_RE.sub("", link_text)).strip().lower()
        if surname not in plain:
            continue
        letters = [c for c in plain if c.isalpha()]
        if letters and letters[0] == first_initial:
            return uid
    return None


def format_bio_lines(bio: str) -> str:
    return textwrap.fill(
        bio,
        width=BIO_WIDTH,
        initial_indent="    ",
        subsequent_indent="    ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def render_entry(
    *,
    name: str,
    work_url: str,
    image_url: str | None,
    bio: str | None,
    email: str | None,
    orcid: str | None,
    scholar_id: str | None,
    cris_url: str | None,
) -> str:
    bio_block = format_bio_lines(bio) if bio else "    TODO: add bio"
    email_line = f"  email: {email}" if email else '  email: ""'
    return (
        f"- name: {name}\n"
        f"  role: PhD Researcher\n"
        f"  group: Researchers\n"
        f'  image_url: "{image_url or ""}"\n'
        f"  themes: []\n"
        f"  bio: >\n"
        f"{bio_block}\n"
        f"  keywords: []\n"
        f'  orcid: "{orcid or ""}"\n'
        f'  github: ""\n'
        f'  google_scholar: "{scholar_id or ""}"\n'
        f'  linkedin: ""\n'
        f'  personal_page: ""\n'
        f'  cris_url: "{cris_url or ""}"\n'
        f'  work_url: "{work_url}"\n'
        f"{email_line}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add new people to people.yaml",
        usage="%(prog)s [\"Full Name\" URL] ..."
    )
    parser.add_argument(
        "pairs", nargs="*", metavar="NAME_OR_URL",
        help="Alternating name and URL pairs",
    )
    args = parser.parse_args()

    if args.pairs:
        if len(args.pairs) % 2 != 0:
            parser.error("arguments must be name/URL pairs (even count)")
        targets: list[tuple[str, str]] = [
            (args.pairs[i], args.pairs[i + 1])
            for i in range(0, len(args.pairs), 2)
        ]
    else:
        targets = NEW_PEOPLE

    existing = PEOPLE_YAML.read_text(encoding="utf-8")
    existing_names = {
        n.strip()
        for n in re.findall(r"(?m)^- name:\s*(.+)$", existing)
    }

    new_blocks: list[str] = []
    for name, work_url in targets:
        if name in existing_names:
            print(f"- {name}: already in people.yaml, skipping")
            continue
        print(f"- {name}: {work_url}")
        body = load_page(work_url)
        image_url = bio = email = orcid = None
        if body is None:
            print("    work_url fetch failed — writing minimal entry")
        else:
            remote_img = extract_profile_image(body, work_url)
            image_url = download_image(remote_img, name) if remote_img else None
            bio = extract_bio(body, name)
            email = extract_email(body)
            orcid = extract_orcid(body)

        cris_url = find_cris_url(name)
        scholar_id = find_scholar_id(name)

        print(f"    image_url      = {image_url or '(none)'}")
        print(f"    bio            = {(bio[:80] + '...') if bio else '(none)'}")
        print(f"    email          = {email or '(none)'}")
        print(f"    orcid          = {orcid or '(none)'}")
        print(f"    cris_url       = {cris_url or '(none)'}")
        print(f"    google_scholar = {scholar_id or '(none)'}")

        new_blocks.append(render_entry(
            name=name, work_url=work_url,
            image_url=image_url, bio=bio,
            email=email, orcid=orcid,
            scholar_id=scholar_id, cris_url=cris_url,
        ))

    if not new_blocks:
        print("\nnothing new to add")
        return 0

    if not existing.endswith("\n"):
        existing += "\n"
    # Blank line before the first new entry and between subsequent entries.
    PEOPLE_YAML.write_text(existing + "\n" + "\n".join(new_blocks), encoding="utf-8")
    print(
        f"\nappended {len(new_blocks)} entrie(s) to "
        f"{PEOPLE_YAML.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
