#!/usr/bin/env python3
"""Resolve DOIs for Pure Portal records, which carry no DOI of their own.

Two passes, cheapest and safest first:
  1. Local. Match against DOIs already in cache/crossref/ by normalised title.
     Free, offline, and exact.
  2. Crossref title query for whatever is left, accepting a match only above a
     similarity threshold.

Pass 2 is the fuzzy one, so every match records its score and the matched title,
and anything below the threshold is reported as unresolved rather than guessed.
A wrong DOI here would silently attribute someone else's paper to the group.

Writes cache/pure_dois.json: {normalised_title: {doi, score, via, matched_title}}.
"""
from __future__ import annotations
import argparse, json, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PURE = ROOT / "cache" / "pure"
CROSSREF = ROOT / "cache" / "crossref"
OUT = ROOT / "cache" / "pure_dois.json"
REVIEW = ROOT / "cache" / "pure_dois_review.json"
MAILTO = "michel.dumontier@maastrichtuniversity.nl"   # Crossref polite pool
UA = f"healthai-website/1.0 (mailto:{MAILTO})"


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# Content words present in one title but not the other. Similarity alone cannot
# separate a correct match from a wrong one: "detection of WANDERING spatio
# temporal recurrent patterns..." scored 0.932 against a genuinely different
# paper, while a correct match with Greek letters transliterated scored 0.933.
# A single distinctive word is the difference, so check words, not just ratio.
STOP = {"a","an","the","of","in","on","for","and","or","to","with","by","from",
        "at","as","is","are","be","its","their","this","that","using","via"}


def word_gap(a: str, b: str) -> set[str]:
    wa = {w for w in norm_title(a).split() if w not in STOP and len(w) > 2}
    wb = {w for w in norm_title(b).split() if w not in STOP and len(w) > 2}
    return (wa | wb) - (wa & wb)


def local_index() -> dict[str, str]:
    """normalised title -> DOI, from the Crossref metadata we already hold."""
    idx: dict[str, str] = {}
    if not CROSSREF.exists():
        return idx
    for f in CROSSREF.glob("*.json"):
        try:
            m = json.loads(f.read_text())
        except Exception:
            continue
        m = m.get("message", m)
        for t in (m.get("title") or []):
            n = norm_title(t)
            if n and m.get("DOI"):
                idx.setdefault(n, m["DOI"].lower())
    return idx


def crossref_lookup(title: str, timeout: int = 40) -> tuple[str, float, str]:
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": 3, "mailto": MAILTO})
    req = urllib.request.Request(f"https://api.crossref.org/works?{q}",
                                 headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    best = ("", 0.0, "")
    want = norm_title(title)
    for it in data.get("message", {}).get("items", []):
        for t in (it.get("title") or []):
            s = SequenceMatcher(None, want, norm_title(t)).ratio()
            if s > best[1]:
                best = ((it.get("DOI") or "").lower(), s, t)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.92,
                    help="minimum title similarity to accept a Crossref match")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between Crossref calls")
    ap.add_argument("--local-only", action="store_true", help="skip the Crossref pass")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    if not PURE.exists():
        print("no cache/pure — run fetch_pure_publications.py first", file=sys.stderr)
        return 1

    titles: dict[str, str] = {}
    for f in sorted(PURE.glob("*.json")):
        for it in json.loads(f.read_text())["items"]:
            n = norm_title(it["title"])
            if n:
                titles.setdefault(n, it["title"])
    print(f"{len(titles)} distinct Pure titles")

    # Pass 0: the exact join against the Pure API index. Same source system, so
    # titles agree exactly and no heuristic is involved. Everything this resolves
    # is strictly better than a Crossref title guess, so it goes first.
    exact_f = ROOT / "cache" / "pure_dois_exact.json"
    out_seed = json.loads(exact_f.read_text()) if exact_f.exists() else {}

    idx = local_index()
    print(f"{len(out_seed)} resolved already by exact Pure join")
    print(f"{len(idx)} titles indexed from the existing Crossref cache")

    out: dict[str, dict] = json.loads(OUT.read_text()) if OUT.exists() else {}
    review: dict[str, dict] = {}
    for k, v in out_seed.items():
        out.setdefault(k, v)
    hit_local = sum(1 for n in titles if n in idx and n not in out)
    for n in titles:
        if n in out:
            continue
        if n in idx:
            out[n] = {"doi": idx[n], "score": 1.0, "via": "local-cache",
                      "matched_title": titles[n]}
    print(f"  resolved locally: {hit_local}")

    todo = [n for n in titles if n not in out]
    if args.limit:
        todo = todo[: args.limit]
    if args.local_only:
        print(f"  {len(todo)} would need Crossref (skipped)")
    else:
        print(f"  querying Crossref for {len(todo)}...")
        ok = low = err = rev = 0
        for i, n in enumerate(todo, 1):
            try:
                doi, score, matched = crossref_lookup(titles[n])
            except Exception as e:
                err += 1
                print(f"    ! {titles[n][:48]}: {type(e).__name__}", file=sys.stderr)
                time.sleep(args.delay)
                continue
            gap = word_gap(titles[n], matched) if doi else {"-"}
            if doi and score >= args.threshold and not gap:
                out[n] = {"doi": doi, "score": round(score, 3), "via": "crossref",
                          "matched_title": matched}
                ok += 1
            elif doi and score >= args.threshold and gap:
                # scores high but a distinctive word differs: needs a human
                review[n] = {"doi": doi, "score": round(score, 3), "via": "crossref-review",
                             "matched_title": matched, "word_gap": sorted(gap)}
                rev += 1
                print(f"    ~ {score:.2f} differs on {sorted(gap)} -- held for review")
                print(f"        pure: {titles[n][:66]}")
                print(f"        xref: {matched[:66]}")
            else:
                low += 1
                print(f"    ? {score:.2f} {titles[n][:46]!r}\n        best: {matched[:62]!r}")
            time.sleep(args.delay)
        print(f"  crossref: {ok} accepted, {rev} held for review, "
              f"{low} below threshold, {err} errors")

    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    if review:
        REVIEW.write_text(json.dumps(review, indent=1, sort_keys=True))
        print(f"{len(review)} held for review -> {REVIEW}")
    print(f"\n{len(out)} / {len(titles)} titles have a DOI  ->  {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
