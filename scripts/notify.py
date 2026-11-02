#!/usr/bin/env python3
"""Tell a human that a scheduled job needs attention.

A CronJob that fails in Kubernetes notifies nobody. Its logs sit in a pod
nobody opens, and the first visible symptom is a page that quietly stopped
changing, which is the failure the weekly jobs exist to prevent.

Two channels, both optional and both best-effort:

  GitHub issue   on $GITHUB_REPO, when $GITHUB_TOKEN is set. Opening an issue
                 emails everyone watching the repo, which puts it in a normal
                 inbox, and it stays open until someone closes it rather than
                 scrolling away. Repeated calls with the same --key do not
                 reopen or re-comment: an issue that is already open is
                 already saying the thing.

  Slack         to $SLACK_RELAY_URL, the in-cluster relay that forwards
                 webhooks through the egress proxy (platform/slack-relay).

NOTHING HERE MAY FAIL THE CALLER. A notifier that takes the job down with it
turns one problem into two, and the job's own result is what matters. Every
failure is printed and swallowed, and the exit status is always 0.

Usage:
    notify.py --level fail --key linkedin-refresh \
              --title "LinkedIn refresh failed" --body "$(tail -40 log)"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
DEFAULT_REPO = "MaastrichtU-IDS/healthai-website"
ICON = {"warn": "⚠️", "fail": "🔴"}


def marker(key: str) -> str:
    return f"<!-- notify-key: {key} -->"


def gh(path: str, token: str, data: dict | None = None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "healthai-website-notify",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def github_issue(level: str, key: str, title: str, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return
    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO).strip()
    try:
        # Listing open issues rather than using the search API: search is
        # eventually consistent and rate-limited separately, and a job that
        # runs weekly would rather read one page than race an index.
        open_issues = gh(f"/repos/{repo}/issues?state=open&per_page=100", token) or []
        for issue in open_issues:
            if marker(key) in (issue.get("body") or ""):
                print(f"notify: issue #{issue['number']} already open for {key}")
                return
        full = f"{body.rstrip()}\n\n{marker(key)}"
        made = gh(f"/repos/{repo}/issues", token,
                  {"title": f"{ICON.get(level, '')} {title}".strip(), "body": full})
        print(f"notify: opened {made['html_url']}")
    except urllib.error.HTTPError as e:
        print(f"notify: GitHub {e.code}: {e.read().decode('utf-8', 'replace')[:200]}",
              file=sys.stderr)
    except Exception as e:
        print(f"notify: GitHub {type(e).__name__}: {e}", file=sys.stderr)


def slack(level: str, title: str, body: str) -> None:
    url = os.environ.get("SLACK_RELAY_URL", "").strip()
    if not url:
        return
    text = f"{ICON.get(level, '')} *{title}*\n```\n{body.strip()[:2500]}\n```"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"notify: slack {r.status}")
    except Exception as e:
        print(f"notify: slack {type(e).__name__}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", choices=["warn", "fail"], default="fail")
    ap.add_argument("--key", required=True, help="dedupe key for the GitHub issue")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    args = ap.parse_args()

    body = args.body or "(no detail)"
    github_issue(args.level, args.key, args.title, body)
    slack(args.level, args.title, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
