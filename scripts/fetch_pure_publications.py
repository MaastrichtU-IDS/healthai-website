#!/usr/bin/env python3
"""Fetch each person's publications from the Pure Portal RSS feed.

Pure is authoritative for Maastricht affiliation. The portal exposes a per-person
feed at /en/persons/<slug>/publications/?format=rss, driven by the cris_url values
already curated in content/people.yaml.

Caveats, all verified against the live service:
  - The feed is capped at 50 items. `size` is ignored and `page=2` is refused, so
    this gives recent output, not full history. The ORCID/Crossref backfill
    remains the historical base.
  - No DOI anywhere in the feed. Resolving DOIs is a separate step.
  - The portal sits behind Cloudflare. A few rapid requests trigger a "Just a
    moment..." interstitial and later requests 403. Hence --delay, and an abort
    as soon as a challenge is seen rather than writing garbage into the cache.
  - The plain HTML page 403s; only ?format=rss is served.

Writes cache/pure/<slug>.json. Raw feeds are kept in cache/pure_rss/<slug>.xml
so parsing can be re-run without refetching.
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.error, urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
PEOPLE = ROOT / "content" / "people.yaml"
CACHE = ROOT / "cache" / "pure"
RAW = ROOT / "cache" / "pure_rss"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0 Safari/537.36")
FEED = "https://cris.maastrichtuniversity.nl/en/persons/{slug}/publications/?format=rss"
PERSON_LINK = re.compile(r'rel="Person"\s+href="[^"]*?/persons/([^/"]+)/?"')


class Challenged(RuntimeError):
    """Cloudflare served an interstitial instead of the feed."""


def slug_of(cris_url: str) -> str | None:
    m = re.search(r"/persons/([^/?#]+)", cris_url or "")
    return m.group(1) if m else None


def fetch(slug: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(FEED.format(slug=slug), headers={"User-Agent": UA})
    try:
        body = urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        body = e.read()
        if b"Just a moment" in body[:400] or e.code == 403:
            raise Challenged(f"HTTP {e.code} for {slug}") from None
        raise
    if b"Just a moment" in body[:400]:
        raise Challenged(f"interstitial for {slug}")
    return body


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse(xml_bytes: bytes) -> list[dict]:
    items = []
    for it in ET.fromstring(xml_bytes).find("channel").findall("item"):
        desc = it.findtext("description") or ""
        # UM co-authors appear as rel="Person" links; external authors are plain text
        coauthors = sorted(set(PERSON_LINK.findall(desc)))
        text = strip_html(desc)
        m = re.search(r"Research output\s*:\s*(.+)$", text)
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "portal_url": (it.findtext("link") or "").strip(),
            "date": (it.findtext("{http://purl.org/dc/elements/1.1/}date")
                     or it.findtext("date") or "").strip(),
            "output_type": (m.group(1).strip() if m else ""),
            "um_coauthor_slugs": coauthors,
            "citation": text,
        })
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=8.0, help="seconds between requests")
    ap.add_argument("--name", help="only people whose name contains this")
    ap.add_argument("--limit", type=int, help="stop after N people")
    ap.add_argument("--reparse", action="store_true", help="reparse cached feeds, no network")
    args = ap.parse_args()

    people = yaml.safe_load(PEOPLE.read_text()) or []
    targets = []
    for p in people:
        s = slug_of(p.get("cris_url", ""))
        if not s:
            continue
        if args.name and args.name.lower() not in (p.get("name", "")).lower():
            continue
        targets.append((p.get("name", s), s))
    if args.limit:
        targets = targets[: args.limit]

    CACHE.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"{len(targets)} people with a cris_url"
          + (" (reparse only)" if args.reparse else f", {args.delay}s apart"))

    total = 0
    for i, (name, slug) in enumerate(targets, 1):
        raw_f = RAW / f"{slug}.xml"
        if not args.reparse:
            if i > 1:
                time.sleep(args.delay)
            try:
                raw_f.write_bytes(fetch(slug))
            except Challenged as e:
                print(f"  ! Cloudflare challenge on {slug} ({e}). Stopping; "
                      f"rerun later or raise --delay.", file=sys.stderr)
                return 2
            except Exception as e:
                print(f"  ! {name}: {type(e).__name__} {e}", file=sys.stderr)
                continue
        if not raw_f.exists():
            continue
        try:
            items = parse(raw_f.read_bytes())
        except ET.ParseError as e:
            print(f"  ! {name}: unparseable feed ({e})", file=sys.stderr)
            continue
        (CACHE / f"{slug}.json").write_text(json.dumps(
            {"slug": slug, "name": name, "count": len(items), "items": items}, indent=1))
        total += len(items)
        flag = "  <-- at 50-item cap" if len(items) >= 50 else ""
        print(f"  {i:>2}. {name[:34]:<36} {len(items):>3} items{flag}")
    print(f"\n{total} publication records cached under {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
