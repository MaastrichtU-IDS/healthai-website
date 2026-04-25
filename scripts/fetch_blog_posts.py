#!/usr/bin/env python3
"""
Fetch blog posts from RSS/Atom feeds listed in content/people.yaml
and sync them into the blog section of content/news.yaml.

Entries added by this script are tagged with source: feed so they can be
replaced cleanly on the next run. Manually added entries (no source field)
are always preserved.

Usage:
    python3 scripts/fetch_blog_posts.py [--dry-run]
"""

import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
NEWS_YAML = ROOT / "content" / "news.yaml"

ATOM_NS = "http://www.w3.org/2005/Atom"


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(text.split())


def truncate(text: str, words: int = 40) -> str:
    parts = text.split()
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]) + "…"


def initials(name: str) -> str:
    return "".join(p[0].upper() for p in name.split() if p)[:2]


def format_date(dt: datetime) -> str:
    return dt.strftime("%-d %b %Y")


def parse_date(s: str) -> datetime:
    if not s:
        return datetime.min
    try:
        # handles ISO 8601 with or without timezone (Python 3.7+)
        return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).replace(tzinfo=None)
    except Exception:
        return datetime.min


def fetch_feed(url: str, author_name: str) -> list[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hair-website-bot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read()
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f"  ERROR parsing XML from {url}: {e}", file=sys.stderr)
        return []

    posts = []

    if root.tag == f"{{{ATOM_NS}}}feed":
        # Atom (Jekyll feed plugin default)
        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            title = (entry.findtext(f"{{{ATOM_NS}}}title") or "").strip()
            link_el = entry.find(f"{{{ATOM_NS}}}link[@rel='alternate']")
            if link_el is None:
                link_el = entry.find(f"{{{ATOM_NS}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
            published = (
                entry.findtext(f"{{{ATOM_NS}}}published")
                or entry.findtext(f"{{{ATOM_NS}}}updated")
                or ""
            )
            summary_el = entry.find(f"{{{ATOM_NS}}}summary")
            if summary_el is None:
                summary_el = entry.find(f"{{{ATOM_NS}}}content")
            summary = strip_html(summary_el.text if summary_el is not None else "")
            categories = [
                c.get("term", "")
                for c in entry.findall(f"{{{ATOM_NS}}}category")
                if c.get("term")
            ]
            posts.append(
                _make_entry(title, link, published, summary, categories, author_name)
            )
    else:
        # RSS 2.0
        channel = root.find("channel") or root
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = strip_html(item.findtext("description") or "")
            categories = [c.text for c in item.findall("category") if c.text]
            posts.append(
                _make_entry(title, link, pub_date, description, categories, author_name)
            )

    return posts


def _make_entry(
    title: str,
    url: str,
    date_str: str,
    excerpt: str,
    categories: list[str],
    author_name: str,
) -> dict:
    dt = parse_date(date_str) if date_str else datetime.min
    return {
        "date": format_date(dt) if dt != datetime.min else "",
        "tag": categories[0] if categories else "Blog",
        "title": title,
        "url": url,
        "excerpt": truncate(excerpt) if excerpt else "",
        "author": author_name,
        "initials": initials(author_name),
        "img": "",
        "source": "feed",
    }


def sort_key(post: dict) -> datetime:
    try:
        return datetime.strptime(post["date"], "%d %b %Y")
    except Exception:
        return datetime.min


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    people = yaml.safe_load(PEOPLE_YAML.read_text())
    news = yaml.safe_load(NEWS_YAML.read_text())

    feed_people = [(p["name"], p["blog_feed"]) for p in people if p.get("blog_feed")]
    if not feed_people:
        print("No blog_feed entries found in people.yaml — nothing to do.")
        return

    fetched: dict[str, dict] = {}  # keyed by URL for deduplication
    for name, feed_url in feed_people:
        print(f"Fetching: {name}  →  {feed_url}")
        posts = fetch_feed(feed_url, name)
        print(f"  {len(posts)} posts found")
        for post in posts:
            if post["url"]:
                fetched[post["url"]] = post

    manual = [p for p in news.get("blog", []) if not p.get("source")]
    feed_posts = list(fetched.values())
    merged = sorted(manual + feed_posts, key=sort_key, reverse=True)

    print(
        f"\n{len(manual)} manual + {len(feed_posts)} from feeds = {len(merged)} total"
    )

    if args.dry_run:
        print("Dry run — not writing.")
        for p in merged:
            src = "(feed)" if p.get("source") else "(manual)"
            print(f"  {src} {p['date']:14s}  {p['title'][:70]}")
        return

    news["blog"] = merged
    NEWS_YAML.write_text(
        yaml.dump(news, allow_unicode=True, sort_keys=False, default_flow_style=False)
    )
    print(f"Written → {NEWS_YAML}")


if __name__ == "__main__":
    main()
