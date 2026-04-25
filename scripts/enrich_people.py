#!/usr/bin/env python3
"""Fetch each person's work_url, cache the page, and fill in image_url + bio.

- Caches HTML to cache/people_pages/<slug>.html (keyed by the work_url host+path).
- Downloads profile photos to assets/people/<name-slug>.jpg.
- Updates `image_url:` and the `bio: >` folded-scalar block in content/people.yaml
  via targeted text edits, preserving the rest of the file's formatting.
"""
from __future__ import annotations

import html
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
CACHE_DIR = ROOT / "cache" / "people_pages"
ASSETS_PEOPLE = ROOT / "assets" / "people"
USER_AGENT = "Mozilla/5.0 (hair-website enrich_people.py)"
BIO_WIDTH = 90
BIO_MAX_CHARS = 700


def slug_for(url: str) -> str:
    p = urllib.parse.urlparse(url)
    path = p.path.strip("/").replace("/", "_") or "index"
    return f"{p.netloc}__{path}"


def name_to_slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def download_image(img_url: str, name: str, refresh: bool = False) -> str | None:
    """Download img_url to assets/people/<slug>.jpg; return the local path string."""
    ASSETS_PEOPLE.mkdir(parents=True, exist_ok=True)
    slug = name_to_slug(name)
    dest = ASSETS_PEOPLE / f"{slug}.jpg"
    if dest.exists() and not refresh:
        print(f"    image     -> {dest.relative_to(ROOT)} (cached)")
        return f"assets/people/{slug}.jpg"
    try:
        data = fetch_bytes(img_url)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"    image download failed: {e}", file=sys.stderr)
        return None
    dest.write_bytes(data)
    print(f"    image     -> {dest.relative_to(ROOT)} ({len(data):,} bytes)")
    return f"assets/people/{slug}.jpg"


def load_page(url: str) -> str | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{slug_for(url)}.html"
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8")
    try:
        body = fetch(url)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None
    path.write_text(body, encoding="utf-8")
    return body


PLACEHOLDER_MARKERS = ("placeholder", "no-portrait", "headerImage", "skin/")


def extract_profile_image(html_text: str, base_url: str) -> str | None:
    """Pick the first <img> that looks like a real profile picture.

    On maastrichtuniversity.nl staff pages, profile pictures live under
    /sites/default/files/styles/<style>/public/ppp/<id>/<file>. We only
    accept images in that folder so we don't pick up placeholder or
    site-chrome images (e.g., CRIS header banners, UM silhouette).
    """
    imgs = re.findall(r'<img[^>]*\ssrc="([^"]+)"', html_text)
    for src in imgs:
        if "/ppp/" not in src:
            continue
        if any(m in src for m in PLACEHOLDER_MARKERS):
            continue
        src = html.unescape(src).split("?", 1)[0]
        return urllib.parse.urljoin(base_url, src)
    return None


def clean_text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    t = html.unescape(t)
    t = t.replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


JUNK_MARKERS = (
    "Research activity",
    "Research output",
    "Cookie",
    "function plumX",
    "Visit ",
    "LinkedIn",
    "Copyright",
)


def extract_bio(html_text: str, person_name: str) -> str | None:
    main_match = re.search(r"<main\b[^>]*>(.*?)</main>", html_text, re.DOTALL | re.IGNORECASE)
    scope = main_match.group(1) if main_match else html_text
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", scope, re.DOTALL | re.IGNORECASE)
    picked: list[str] = []
    for raw in paragraphs:
        text = clean_text(raw)
        if len(text) < 80:
            continue
        if any(m in text for m in JUNK_MARKERS):
            continue
        # Only strip the name if it appears duplicated at the start
        # (e.g. "Yuyang Yan Yuyang Yan is a Ph.D. Candidate..."). A single
        # occurrence like "Dr. Christopher Brewster is Professor of..." is
        # the natural bio opener and should be preserved.
        nm = person_name.strip()
        dup = f"{nm} {nm} "
        if text.lower().startswith(dup.lower()):
            text = text[len(dup):].lstrip()
        picked.append(text)
        if sum(len(p) for p in picked) >= 400:
            break
    if not picked:
        return None
    bio = " ".join(picked)
    if len(bio) > BIO_MAX_CHARS:
        cut = bio.rfind(". ", 0, BIO_MAX_CHARS)
        bio = bio[: cut + 1] if cut > 200 else bio[:BIO_MAX_CHARS].rstrip() + "..."
    return bio


def format_bio_block(bio: str) -> str:
    wrapped = textwrap.fill(
        bio,
        width=BIO_WIDTH,
        initial_indent="    ",
        subsequent_indent="    ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "  bio: >\n" + wrapped + "\n"


ENTRY_SPLIT_RE = re.compile(r"(?m)^(?=- name:)")


def split_entries(text: str) -> list[str]:
    parts = ENTRY_SPLIT_RE.split(text)
    # First element is any preamble before the first entry (empty here).
    return parts


def replace_image_url(entry: str, new_url: str) -> str:
    return re.sub(
        r'(?m)^(\s{2}image_url:)\s*"[^"]*"\s*$',
        lambda m: f'{m.group(1)} "{new_url}"',
        entry,
        count=1,
    )


BIO_BLOCK_RE = re.compile(
    r"(?ms)^(\s{2}bio:\s*>[^\n]*\n)((?:\s{4,}[^\n]*\n)+)"
)


def replace_bio_block(entry: str, new_bio: str) -> str:
    new_block = format_bio_block(new_bio)
    return BIO_BLOCK_RE.sub(new_block, entry, count=1)


def main() -> int:
    raw = PEOPLE_YAML.read_text(encoding="utf-8")
    people = yaml.safe_load(raw)
    entries = split_entries(raw)
    if len(entries) - 1 != len(people):
        print(
            f"WARN: parsed {len(people)} people but split produced {len(entries)-1} entry blocks",
            file=sys.stderr,
        )

    updated_entries = [entries[0]]
    for person, block in zip(people, entries[1:]):
        name = person.get("name", "<unknown>")
        work_url = (person.get("work_url") or "").strip()
        if not work_url:
            print(f"- {name}: no work_url, skipping")
            updated_entries.append(block)
            continue
        print(f"- {name}: {work_url}")
        body = load_page(work_url)
        if body is None:
            updated_entries.append(block)
            continue

        img = extract_profile_image(body, work_url)
        if img:
            local_path = download_image(img, name)
            if local_path:
                block = replace_image_url(block, local_path)
            else:
                block = replace_image_url(block, img)
        else:
            print("    image_url: none found")

        bio = extract_bio(body, name)
        if bio:
            block = replace_bio_block(block, bio)
            print(f"    bio      -> {bio[:80]}... ({len(bio)} chars)")
        else:
            print("    bio: no usable paragraph found")

        updated_entries.append(block)

    new_raw = "".join(updated_entries)
    if new_raw != raw:
        PEOPLE_YAML.write_text(new_raw, encoding="utf-8")
        print(f"\nwrote {PEOPLE_YAML.relative_to(ROOT)}")
    else:
        print("\nno changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
