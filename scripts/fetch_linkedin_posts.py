#!/usr/bin/env python3
"""
Fetch LinkedIn posts from the DACS organisation page and keep the ones that
mention someone in content/people.yaml.

The DACS page carries departmental news, so the whole feed is too broad for
this site. A post is kept only when it names a member of the group, either as
a real LinkedIn mention (@[Name](urn:li:person:...)) or as plain text in the
body. Near-duplicate reposts of the same announcement are collapsed to the
most recent one, because conference calls for papers are posted over and over
with a new deadline each time.

Images are mirrored into assets/linkedin/ because the LinkedIn CDN hands out
URLs that expire within hours.

No personal identifiers are written out. The person URNs that appear in the
mention markup and in the like and comment lists stay in memory; only the
display names of people already listed on this site reach the YAML.

Reads the OAuth token written by scripts/linkedin_auth.py.

Usage:
    python3 scripts/fetch_linkedin_posts.py [--dry-run] [--scan N]
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PEOPLE_YAML = ROOT / "content" / "people.yaml"
OUT_YAML = ROOT / "content" / "linkedin.generated.yaml"
IMG_DIR = ROOT / "assets" / "linkedin"
TOKEN_FILE = ROOT / ".linkedin_token.json"

ORG_URN = "urn:li:organization:27209119"
ORG_NAME = "Department of Advanced Computing Sciences, Maastricht University"
ORG_URL = "https://www.linkedin.com/company/27209119/"

API = "https://api.linkedin.com"
# 202610 is the oldest version LinkedIn still accepts; everything earlier
# answers 426 NONEXISTENT_VERSION.
LI_VERSION = "202610"

# The image CDN answers 403 to anything that does not look like a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PAGE_SIZE = 25  # 50 at a time trips SSL: UNEXPECTED_EOF_WHILE_READING
DEFAULT_SCAN = 150
KEEP = 40  # how many kept posts to publish

MENTION = re.compile(r"@\[([^\]]+)\]\(urn:li:(?:person|organization):[^)]+\)")
HASHTAG = re.compile(r"\{hashtag\|\\#\|([^}]+)\}")


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def token() -> str:
    """An access token, from the environment or from the local token file.

    Three routes, in order:

      LINKEDIN_ACCESS_TOKEN   used as is.
      LINKEDIN_REFRESH_TOKEN  exchanged for a fresh access token, with
          LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET. This is the cluster's
          route: access tokens last 60 days and a weekly job that stored one
          would stop working two months in, silently from the outside. The
          refresh token lasts a year, so the exchange happens every run and
          nothing in Vault expires between re-authorisations.
      .linkedin_token.json    written by scripts/linkedin_auth.py, for a laptop.
    """
    if os.environ.get("LINKEDIN_ACCESS_TOKEN"):
        return os.environ["LINKEDIN_ACCESS_TOKEN"].strip()

    refresh = os.environ.get("LINKEDIN_REFRESH_TOKEN", "").strip()
    if refresh:
        cid = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
        secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
        if not (cid and secret):
            die("LINKEDIN_REFRESH_TOKEN needs LINKEDIN_CLIENT_ID and _SECRET too")
        return refreshed(refresh, cid, secret)

    if not TOKEN_FILE.exists():
        die(f"no token at {TOKEN_FILE}; run scripts/linkedin_auth.py first")
    return json.loads(TOKEN_FILE.read_text())["access_token"]


def refreshed(refresh: str, client_id: str, client_secret: str) -> str:
    """Trade the refresh token for an access token."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    try:
        req = urllib.request.Request(TOKEN_URL, data)
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        die(f"refresh rejected ({e.code}): {e.read().decode('utf-8', 'replace')[:200]}")
    # LinkedIn does not rotate the refresh token, it keeps the original
    # expiry. So this one dies on a date no job can move, and the warning has
    # to be loud enough to act on before it does.
    left = int(tok.get("refresh_token_expires_in", 0)) // 86400
    # The wrapper turns this into a notification; a warning in a pod log that
    # nobody opens is the same as no warning at all.
    days_file = os.environ.get("LINKEDIN_TOKEN_DAYS_FILE", "").strip()
    if days_file:
        try:
            Path(days_file).write_text(str(left))
        except OSError as e:
            print(f"  could not write {days_file}: {e}", file=sys.stderr)
    if left and left < 30:
        print(
            f"WARNING: the refresh token expires in {left} days. Re-run "
            "scripts/linkedin_auth.py and update Vault, or this job stops.",
            file=sys.stderr,
        )
    print(f"access token refreshed (refresh token good for {left} more days)",
          file=sys.stderr)
    return tok["access_token"]


def api_get(path: str, tok: str, tries: int = 3):
    url = API + path
    headers = {
        "Authorization": f"Bearer {tok}",
        "LinkedIn-Version": LI_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            if e.code == 401:
                die("token rejected (401); re-run scripts/linkedin_auth.py")
            if e.code == 403:
                die(f"token lacks the scope for {path} (403): {body}")
            if attempt == tries - 1:
                print(f"  HTTP {e.code} on {path}: {body}", file=sys.stderr)
                return None
        except Exception as e:
            if attempt == tries - 1:
                print(f"  {type(e).__name__} on {path}: {e}", file=sys.stderr)
                return None
        time.sleep(2 * (attempt + 1))
    return None


def fold(s: str) -> str:
    """Casefold and strip accents so 'Ozge' matches 'Özge'."""
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def load_people() -> tuple[dict[str, str], set[str]]:
    """Names to match on, and the addresses this site already publishes."""
    data = yaml.safe_load(PEOPLE_YAML.read_text()) or []
    people = {fold(p["name"]): p["name"] for p in data if isinstance(p, dict) and p.get("name")}
    emails = {p["email"].strip().lower() for p in data
              if isinstance(p, dict) and (p.get("email") or "").strip()}
    return people, emails


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def clean_text(raw: str, allowed_emails: set[str] | None = None) -> str:
    """Turn LinkedIn's post markup into plain text.

    Email addresses are dropped unless the site already publishes that exact
    address on its people page. A post written by the department is not a
    reason for this site to start republishing someone's address.
    """
    t = raw or ""
    t = MENTION.sub(r"\1", t)
    t = HASHTAG.sub(r"#\1", t)
    # LinkedIn backslash-escapes punctuation, so this has to come before the
    # address match: the raw text reads bnaic2026\\-dacs@example\\.nl.
    t = re.sub(r"\\([^\w\s])", r"\1", t)
    allow = allowed_emails or set()
    t = EMAIL.sub(lambda m: m.group(0) if m.group(0).lower() in allow else "", t)
    t = re.sub(r" +([,.;:)])", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def mentioned(raw: str, people: dict[str, str]) -> list[str]:
    """Group members this post names, by mention markup or in plain text."""
    found = {}
    for name in MENTION.findall(raw or ""):
        key = fold(name)
        if key in people:
            found[people[key]] = True
    body = fold(clean_text(raw))
    for key, name in people.items():
        if key in body:
            found[name] = True
    return sorted(found)


STOPWORDS = set(
    """a an and are as at be by for from has have in is it its of on or our that the
    this to was we were will with you your""".split()
)

SIMILARITY = 0.45  # above this, two posts are the same announcement


def shingle(text: str) -> set[str]:
    """Content words of a post, for comparing one announcement with another.

    Links, numbers and punctuation go: a call for papers posted in May and
    again in July with a new deadline differs in little else.
    """
    t = fold(text)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^a-z\s]", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in STOPWORDS}


def same_announcement(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    # Overlap against the shorter post: a reminder is a trimmed repost, so it
    # sits almost entirely inside the original and Jaccard would miss it.
    return len(a & b) / min(len(a), len(b)) >= SIMILARITY


def fetch_posts(tok: str, want: int) -> list[dict]:
    posts, start = [], 0
    while len(posts) < want:
        q = urllib.parse.urlencode(
            {
                "q": "author",
                "author": ORG_URN,
                "count": PAGE_SIZE,
                "start": start,
                "sortBy": "LAST_MODIFIED",
            }
        )
        page = api_get(f"/rest/posts?{q}", tok)
        if not page:
            break
        elements = page.get("elements", [])
        if not elements:
            break
        posts.extend(elements)
        start += PAGE_SIZE
        print(f"  fetched {len(posts)} posts", file=sys.stderr)
        time.sleep(0.5)
    return posts[:want]


def image_urn(post: dict) -> str | None:
    content = post.get("content") or {}
    media = content.get("media") or {}
    if isinstance(media.get("id"), str) and media["id"].startswith("urn:li:image:"):
        return media["id"]
    for item in (content.get("multiImage") or {}).get("images") or []:
        if isinstance(item.get("id"), str):
            return item["id"]
    return None


def mirror_image(urn: str, tok: str, dry_run: bool) -> str | None:
    """Download the image behind an image URN; return its repo-relative path."""
    name = hashlib.sha1(urn.encode()).hexdigest()[:16] + ".jpg"
    dest = IMG_DIR / name
    rel = f"assets/linkedin/{name}"
    # Check the mirror before asking LinkedIn: /rest/images has a daily cap
    # and an image already on disk never changes.
    if dest.exists() or dry_run:
        return rel
    meta = api_get("/rest/images/" + urllib.parse.quote(urn, safe=""), tok)
    if not meta:
        return None
    url = meta.get("downloadUrl")
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except Exception as e:
        print(f"  image {urn}: {type(e).__name__}", file=sys.stderr)
        return None
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    shrink(dest)
    return rel


def shrink(path: Path, width: int = 900, quality: int = 55) -> None:
    """Downscale a mirrored image so the repo does not carry 1280px originals.

    Pillow first, so the cluster job and a laptop produce the same bytes; sips
    second, for a Mac without Pillow. With neither, the full-size image is
    kept and the page still works, the checkout is just larger.
    """
    try:
        from PIL import Image
    except ImportError:
        if shutil.which("sips"):
            subprocess.run(
                ["sips", "-Z", str(width), "-s", "format", "jpeg",
                 "-s", "formatOptions", str(quality), str(path)],
                capture_output=True,
                check=False,
            )
        return
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize(
                    (width, round(im.height * width / im.width)), Image.LANCZOS)
            im.save(path, "JPEG", quality=quality, optimize=True)
    except Exception as e:
        print(f"  resize {path.name}: {type(e).__name__}", file=sys.stderr)


def engagement(urns: list[str], tok: str) -> dict[str, dict]:
    """Like and comment counts, 20 posts per request.

    The response also carries the URNs of everyone who liked or commented.
    Those are dropped here; only the counts are kept.
    """
    out = {}
    for i in range(0, len(urns), 20):
        chunk = urns[i : i + 20]
        ids = ",".join(urllib.parse.quote(u, safe="") for u in chunk)
        page = api_get(f"/v2/socialActions?ids=List({ids})", tok)
        if not page:
            continue
        for urn, body in (page.get("results") or {}).items():
            out[urn] = {
                "likes": (body.get("likesSummary") or {}).get("totalLikes", 0),
                "comments": (body.get("commentsSummary") or {}).get("totalFirstLevelComments", 0),
            }
        time.sleep(0.5)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--scan", type=int, default=DEFAULT_SCAN, help="posts to scan")
    ap.add_argument("--keep", type=int, default=KEEP, help="posts to publish")
    args = ap.parse_args()

    people, emails = load_people()
    print(f"{len(people)} names from people.yaml", file=sys.stderr)

    tok = token()
    raw = fetch_posts(tok, args.scan)
    if not raw:
        die("no posts returned")
    print(f"scanned {len(raw)} posts", file=sys.stderr)

    kept = []
    for p in raw:
        text = p.get("commentary") or ""
        who = mentioned(text, people)
        if not who:
            continue
        clean = clean_text(text, emails)
        words = shingle(clean)
        # Posts arrive newest first, so the one already kept is the latest of
        # its series and the repost folds into it.
        prior = next((e for e in kept if same_announcement(words, e["words"])), None)
        if prior:
            prior["repeats"] += 1
            continue
        created = p.get("createdAt") or p.get("publishedAt") or 0
        entry = {
            "urn": p.get("id"),
            "date": datetime.fromtimestamp(created / 1000, timezone.utc).strftime("%-d %b %Y"),
            "iso": datetime.fromtimestamp(created / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "text": clean,
            "people": who,
            "image_urn": image_urn(p),
            "words": words,
            "repeats": 1,
        }
        kept.append(entry)

    kept.sort(key=lambda e: e["iso"], reverse=True)
    collapsed = sum(e["repeats"] - 1 for e in kept)
    print(
        f"kept {len(kept)} mentioning a group member "
        f"({collapsed} near-duplicates collapsed)",
        file=sys.stderr,
    )
    kept = kept[: args.keep]

    stats = engagement([e["urn"] for e in kept if e["urn"]], tok)
    for e in kept:
        s = stats.get(e["urn"], {})
        e["likes"] = s.get("likes", 0)
        e["comments"] = s.get("comments", 0)

    for e in kept:
        if e["image_urn"]:
            e["image"] = mirror_image(e["image_urn"], tok, args.dry_run)

    items = []
    for e in kept:
        item = {
            "date": e["date"],
            "url": f"https://www.linkedin.com/feed/update/{e['urn']}/" if e["urn"] else None,
            "text": e["text"],
            "people": e["people"],
            "likes": e["likes"],
            "comments": e["comments"],
        }
        if e.get("image"):
            item["image"] = e["image"]
        if e["repeats"] > 1:
            item["reposts"] = e["repeats"]
        items.append({k: v for k, v in item.items() if v is not None})

    doc = {
        "source": {
            "name": ORG_NAME,
            "url": ORG_URL,
            # The refresh job puts this file back when nothing but this line
            # moved, so what it actually dates is the last change, not the
            # last run. news.html says so.
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "note": (
                "Posts from the DACS page that name someone on this site. "
                "Repeats of the same announcement are collapsed."
            ),
        },
        "social": items,
    }

    if args.dry_run:
        print(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=88))
        return

    OUT_YAML.write_text(
        "# Generated by scripts/fetch_linkedin_posts.py. Do not edit by hand.\n"
        + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=88)
    )
    print(f"wrote {OUT_YAML.relative_to(ROOT)} with {len(items)} posts", file=sys.stderr)


if __name__ == "__main__":
    main()
