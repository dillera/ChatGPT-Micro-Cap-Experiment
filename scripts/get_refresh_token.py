#!/usr/bin/env python3
"""Obtain a TastyTrade refresh token via the OAuth2 Authorization Code flow.

Usage:
    uv run python scripts/get_refresh_token.py

What it does:
  1. Opens your browser to TastyTrade's OAuth authorization page
  2. You log in there (TT handles username, password, 2FA — not this script)
  3. TT redirects back to http://localhost:<PORT>/callback with ?code=...
  4. This script exchanges that code for a refresh token
  5. Writes TT_REFRESH=<token> to your .env file

Requires TT_SECRET in .env (your OAuth client secret from TT developer portal).
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_URL = "https://api.tastyworks.com"
CALLBACK_PORT = 18085
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


# ---------------------------------------------------------------------------
# Local callback server — captures the ?code= from TT's redirect
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
            body = b"<h2>Authorization successful! You can close this tab.</h2>"
        else:
            body = b"<h2>Unexpected callback. Check the terminal.</h2>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress request logs


def _start_callback_server() -> http.server.HTTPServer:
    server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def _load_env_value(key: str) -> str | None:
    """Read a key from .env without loading the whole file into os.environ."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].strip()
    return None


def _write_env_value(key: str, value: str) -> None:
    """Update or append key=value in .env."""
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


def main():
    # --- 1. Get client secret ---
    client_secret = _load_env_value("TT_SECRET") or os.getenv("TT_SECRET")
    if not client_secret:
        client_secret = input("Enter your TastyTrade OAuth client secret (TT_SECRET): ").strip()
    if not client_secret:
        print("Error: TT_SECRET is required.")
        sys.exit(1)

    # --- 2. Build authorization URL ---
    auth_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_secret,  # TT uses client_secret as client_id
        "redirect_uri": REDIRECT_URI,
    })
    auth_url = f"{API_URL}/oauth/authorize?{auth_params}"

    # --- 3. Start callback server, open browser ---
    print(f"\nStarting local callback server on port {CALLBACK_PORT}...")
    _start_callback_server()

    print(f"\nOpening browser for TastyTrade login...")
    print(f"  URL: {auth_url}\n")
    webbrowser.open(auth_url)
    print("Waiting for authorization callback... (log in to TastyTrade in the browser)")

    # Wait for the callback handler to set _auth_code (it runs in daemon thread)
    import time
    for _ in range(120):  # 2-minute timeout
        if _auth_code or _server_error:
            break
        time.sleep(0.5)

    if _server_error:
        print(f"\nAuthorization error from TastyTrade: {_server_error}")
        sys.exit(1)

    if not _auth_code:
        print("\nTimed out waiting for authorization. Did the browser open?")
        print(f"Try opening this URL manually: {auth_url}")
        sys.exit(1)

    print(f"\nGot authorization code: {_auth_code[:12]}...")

    # --- 4. Exchange code for tokens ---
    print("Exchanging authorization code for refresh token...")
    resp = httpx.post(
        f"{API_URL}/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_secret": client_secret,
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    if resp.status_code not in (200, 201):
        print(f"\nToken exchange failed ({resp.status_code}):")
        print(resp.text)
        sys.exit(1)

    data = resp.json()
    refresh_token = data.get("refresh_token") or data.get("refresh-token")
    access_token = data.get("access_token") or data.get("access-token")

    if not refresh_token:
        print("\nNo refresh_token in response:")
        print(data)
        # Fall back to access token as a session token (some providers omit refresh)
        if access_token:
            print("\nOnly access_token found — using it as TT_REFRESH (shorter-lived).")
            refresh_token = access_token
        else:
            sys.exit(1)

    # --- 5. Write to .env ---
    print(f"\nSuccess! Refresh token received.")
    _write_env_value("TT_REFRESH", refresh_token)

    print(f"\n{'='*60}")
    print("Done! Your .env has been updated with TT_REFRESH.")
    print("Refresh tokens don't expire — keep .env safe and out of git.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
