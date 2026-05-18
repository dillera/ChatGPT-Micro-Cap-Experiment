#!/usr/bin/env python3
"""Obtain a TastyTrade refresh token via OAuth2 Authorization Code + PKCE flow.

Usage:
    uv run python scripts/get_refresh_token.py

What it does:
  1. Opens your browser to https://my.tastytrade.com/auth.html
  2. You log in there (TT handles username, password, and 2FA)
  3. TT redirects to http://localhost:18085/callback?code=...
  4. Exchanges the code for a refresh token via /oauth/token
  5. Writes TT_REFRESH=<token> to your .env file

Requires TT_SECRET in .env (OAuth client secret from TT developer portal).
Discovery doc: https://api.tastytrade.com/.well-known/openid-configuration
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# From OpenID discovery: https://api.tastytrade.com/.well-known/openid-configuration
AUTH_ENDPOINT = "https://my.tastytrade.com/auth.html"
TOKEN_ENDPOINT = "https://api.tastytrade.com/oauth/token"
CALLBACK_PORT = 18085
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

_auth_code: str | None = None
_server_error: str | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code, _server_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _server_error = params["error"][0]
            body = b"<h2>Authorization failed. Check the terminal.</h2>"
        elif "code" in params:
            _auth_code = params["code"][0]
            body = b"<h2>Authorized! You can close this tab.</h2>"
        else:
            body = b"<h2>Unexpected callback - check the terminal.</h2>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_callback_server() -> None:
    server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def _read_env_value(key: str) -> str | None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].strip()
    return None


def _write_env_value(key: str, value: str) -> None:
    env_file = ROOT / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    updated = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(new_lines) + "\n")
    print(f"  Written {key} to .env")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Get client ID and secret
    client_id = _read_env_value("TT_CLIENT_ID") or os.getenv("TT_CLIENT_ID")
    client_secret = _read_env_value("TT_SECRET") or os.getenv("TT_SECRET")
    if not client_id:
        client_id = input("Enter your TastyTrade OAuth client ID (TT_CLIENT_ID): ").strip()
    if not client_secret:
        client_secret = input("Enter your TastyTrade OAuth client secret (TT_SECRET): ").strip()
    if not client_id or not client_secret:
        print("Error: TT_CLIENT_ID and TT_SECRET are both required.")
        sys.exit(1)

    # 2. Generate PKCE pair and state
    code_verifier, code_challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    # 3. Build authorization URL
    auth_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "read trade offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_ENDPOINT}?{auth_params}"

    # 4. Start callback server and open browser
    _start_callback_server()
    print(f"\nOpening TastyTrade login in your browser...")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)
    print("Waiting for you to log in... (2-minute timeout)")

    # 5. Wait for callback
    for _ in range(240):  # 2-minute timeout at 0.5s intervals
        if _auth_code or _server_error:
            break
        time.sleep(0.5)

    if _server_error:
        print(f"\nAuthorization error: {_server_error}")
        sys.exit(1)

    if not _auth_code:
        print("\nTimed out waiting for callback. Did the browser open?")
        print(f"Try opening manually: {auth_url}")
        sys.exit(1)

    print(f"Got authorization code.")

    # 6. Exchange code for tokens
    print("Exchanging code for refresh token...")
    resp = httpx.post(
        TOKEN_ENDPOINT,
        json={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    if resp.status_code not in (200, 201):
        print(f"\nToken exchange failed ({resp.status_code}):")
        print(resp.text)
        sys.exit(1)

    data = resp.json()
    refresh_token = data.get("refresh_token") or data.get("refresh-token")

    if not refresh_token:
        print("\nNo refresh_token in response. Full response:")
        print(data)
        sys.exit(1)

    # 7. Write to .env
    _write_env_value("TT_REFRESH", refresh_token)

    print(f"\n{'='*60}")
    print("Done! TT_REFRESH written to .env.")
    print("Refresh tokens don't expire — keep .env out of git.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
