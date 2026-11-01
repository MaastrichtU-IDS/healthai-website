#!/usr/bin/env python3
"""Obtain a LinkedIn access token for the group's organisation page.

Runs the three-legged OAuth flow entirely on this machine. It starts a local
listener, opens LinkedIn in the browser, catches the redirect, and exchanges
the code for a token. Nothing is pasted anywhere and the client secret never
leaves this process except in the token request to LinkedIn itself.

Needs, in .env or the environment:
    LINKEDIN_CLIENT_ID
    LINKEDIN_CLIENT_SECRET

and the app must list this exact redirect URL:
    http://localhost:8899/callback

WHY A TOKEN EXPIRES AND WHAT THAT MEANS. LinkedIn access tokens last 60
days. Refresh tokens last a year but are issued only to approved Marketing
Developer Platform partners; without that status there is no refresh and a
human must run this again every 60 days. Whatever consumes the token has to
fail loudly when it lapses rather than quietly serve stale posts.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REDIRECT = "http://localhost:8899/callback"
SCOPE = "r_organization_social"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def cfg(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    sys.exit(f"missing {name}: set it in the environment or add {name}= to .env")


class Catcher(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        Catcher.result = {k: v[0] for k, v in q.items()}
        ok = "code" in Catcher.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>Authorised. You can close this tab.</h2>" if ok
            else b"<h2>Authorisation failed. Check the terminal.</h2>"
        )

    def log_message(self, *_):  # keep the console clean
        pass


def main() -> int:
    client_id, client_secret = cfg("LINKEDIN_CLIENT_ID"), cfg("LINKEDIN_CLIENT_SECRET")
    state = secrets.token_urlsafe(24)

    server = http.server.HTTPServer(("127.0.0.1", 8899), Catcher)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "state": state,
        "scope": SCOPE,
    })
    print("Opening LinkedIn to authorise. Sign in as a Page admin.\n")
    print(url + "\n")
    webbrowser.open(url)

    # handle_request() in the thread above serves exactly one request, so
    # this just waits for the redirect to land.
    print("Waiting for the redirect on " + REDIRECT + " ...")
    while not Catcher.result:
        threading.Event().wait(0.3)
    res = Catcher.result

    if res.get("error"):
        return fail(f"LinkedIn returned an error: {res['error']} {res.get('error_description','')}")
    # The state check is what stops a different page's redirect being accepted.
    if res.get("state") != state:
        return fail("state mismatch; the redirect did not come from this request")
    if "code" not in res:
        return fail(f"no code in the redirect: {res}")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": res["code"],
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(
                TOKEN_URL, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}),
            timeout=30).read()
    except urllib.error.HTTPError as e:
        return fail(f"token exchange failed: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")

    tok = json.loads(raw)
    days = round(tok.get("expires_in", 0) / 86400)
    print(f"\nAccess token obtained. Expires in {tok.get('expires_in')} s (~{days} days).")
    if "refresh_token" in tok:
        rdays = round(tok.get("refresh_token_expires_in", 0) / 86400)
        print(f"Refresh token also issued, valid ~{rdays} days.")
    else:
        print("NO refresh token issued, so this app is not an approved Marketing")
        print("Developer Platform partner. Re-run this before the token expires.")

    out = ROOT / ".linkedin_token.json"
    out.write_text(json.dumps(tok, indent=1))
    out.chmod(0o600)
    print(f"\nWritten to {out} (chmod 600, gitignored).")
    print("Put the access_token into Vault as LINKEDIN_TOKEN for the cluster job.")
    return 0


def fail(msg: str) -> int:
    print(f"\nFAILED: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
