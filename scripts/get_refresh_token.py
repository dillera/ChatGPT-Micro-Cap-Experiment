#!/usr/bin/env python3
"""One-time script to obtain a tastytrade refresh token.

Supports two 2FA modes:
  - TOTP (authenticator app): code is sent in the login body as one-time-password
  - Device challenge (email/SMS): challenge-token header flow

Usage:
    python scripts/get_refresh_token.py
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import get_settings

API_URL = "https://api.tastyworks.com"


def main():
    settings = get_settings()

    client_secret = settings.tt_secret
    if not client_secret:
        client_secret = input("Enter your tastytrade Client Secret (TT_SECRET): ").strip()
    if not client_secret:
        print("Error: Client secret is required")
        sys.exit(1)

    print("\nEnter your tastytrade account credentials:")
    username = input("Username (email): ").strip()
    password = getpass.getpass("Password: ")

    if not username or not password:
        print("Error: Username and password are required")
        sys.exit(1)

    client = httpx.Client(
        base_url=API_URL,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    # Step 1: attempt login without 2FA code
    print("\nLogging in to tastytrade...")
    r = client.post("/sessions", json={"login": username, "password": password, "remember-me": True})

    if r.status_code in (200, 201):
        _handle_success(client, client_secret, r.json())
        return

    body = r.json()
    error_code = body.get("error", {}).get("code", "")

    # TOTP flow: server tells us a 2FA code is required
    if r.status_code in (401, 422, 403) and (
        "mfa" in error_code.lower()
        or "two_factor" in error_code.lower()
        or "two factor" in body.get("error", {}).get("message", "").lower()
        or "one-time" in body.get("error", {}).get("message", "").lower()
    ):
        print("Two-factor authentication required.")
        _handle_totp(client, username, password, client_secret)
        return

    # Device challenge flow (new device verification via email/SMS)
    if r.status_code == 403 and error_code == "device_challenge_required":
        print("Device verification required (new device — check email/SMS).")
        _handle_device_challenge(client, username, password, client_secret, r)
        return

    # Unknown failure — show full response to help debug
    print(f"\nLogin failed ({r.status_code}): {r.text}")
    print("\nIf you have an authenticator app, run again and enter your TOTP code when prompted.")
    sys.exit(1)


def _handle_totp(client: httpx.Client, username: str, password: str, client_secret: str):
    """TOTP flow: include one-time-password directly in the login request body."""
    code = input("Enter your authenticator app code: ").strip()
    if not code:
        print("Error: Code is required")
        sys.exit(1)

    print("Verifying TOTP code...")
    r = client.post(
        "/sessions",
        json={
            "login": username,
            "password": password,
            "one-time-password": code,
            "remember-me": True,
        },
    )

    if r.status_code in (200, 201):
        _handle_success(client, client_secret, r.json())
        return

    print(f"TOTP login failed ({r.status_code}): {r.text}")
    sys.exit(1)


def _handle_device_challenge(
    client: httpx.Client,
    username: str,
    password: str,
    client_secret: str,
    initial_response: httpx.Response,
):
    """Handle 2FA challenge. Tries TOTP (authenticator app) first, then email/SMS fallback."""
    challenge_token = (
        initial_response.headers.get("x-tastyworks-challenge-token")
        or initial_response.json().get("error", {}).get("redirect", {}).get("challenge_token")
        or initial_response.json().get("challenge_token")
    )

    if not challenge_token:
        print("Could not find challenge token in response:")
        print(json.dumps(initial_response.json(), indent=2))
        sys.exit(1)

    print(f"Got challenge token: {challenge_token[:20]}...")

    # Try TOTP first — submit authenticator code directly without triggering email/SMS
    code = input("Enter your authenticator app code (or press Enter to receive email/SMS code): ").strip()

    if code:
        # Attempt TOTP: include challenge token + one-time-password in login body
        r = client.post(
            "/sessions",
            json={
                "login": username,
                "password": password,
                "one-time-password": code,
                "remember-me": True,
            },
            headers={"X-Tastyworks-Challenge-Token": challenge_token},
        )
        if r.status_code in (200, 201):
            _handle_success(client, client_secret, r.json())
            return

        # Some tastytrade setups want the code as a header instead
        r = client.post(
            "/sessions",
            json={"login": username, "password": password, "remember-me": True},
            headers={
                "X-Tastyworks-Challenge-Token": challenge_token,
                "X-Tastyworks-OTC": code,
            },
        )
        if r.status_code in (200, 201):
            _handle_success(client, client_secret, r.json())
            return

        print(f"TOTP attempt 1 failed ({r.status_code}): {r.text}")
        print(f"TOTP attempt 2 failed ({r.status_code}): {r.text}")
        print("\nDebug — full response headers:")
        print(dict(r.headers))
        print("Falling back to email/SMS challenge...")

    # Email/SMS fallback: trigger tastytrade to send a code
    r2 = client.post(
        "/device-challenge",
        headers={"X-Tastyworks-Challenge-Token": challenge_token},
    )
    if r2.status_code not in (200, 201, 202):
        print(f"Failed to send email/SMS code ({r2.status_code}): {r2.text}")
        sys.exit(1)

    sms_code = input("Enter the code sent to your email/phone: ").strip()
    if not sms_code:
        print("Error: Code is required")
        sys.exit(1)

    r3 = client.post(
        "/sessions",
        json={"login": username, "password": password, "remember-me": True},
        headers={
            "X-Tastyworks-Challenge-Token": challenge_token,
            "X-Tastyworks-OTC": sms_code,
        },
    )
    if r3.status_code not in (200, 201):
        print(f"Login with email/SMS code failed ({r3.status_code}): {r3.text}")
        sys.exit(1)

    _handle_success(client, client_secret, r3.json())


def _handle_success(client: httpx.Client, client_secret: str, response_body: dict):
    """Extract session token and obtain a refresh token."""
    data = response_body.get("data", response_body)

    # Tastytrade sometimes includes the refresh token directly in the login response
    refresh_token = (
        data.get("refresh-token")
        or data.get("refresh_token")
    )
    if refresh_token:
        _print_result(client_secret, refresh_token)
        return

    session_token = (
        data.get("session-token")
        or data.get("session_token")
        or data.get("token")
    )
    if not session_token:
        print("Login succeeded but could not find session token:")
        print(json.dumps(response_body, indent=2))
        sys.exit(1)

    print("Login successful — exchanging session for refresh token...")

    r = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_secret": client_secret,
            "refresh_token": session_token,
        },
    )
    if r.status_code in (200, 201):
        token_data = r.json()
        refresh_token = token_data.get("refresh_token") or token_data.get("refresh-token")
        if refresh_token:
            _print_result(client_secret, refresh_token)
            return

    # Session token may itself be usable as a refresh token
    _print_result(client_secret, session_token)


def _print_result(client_secret: str, refresh_token: str):
    print(f"\n{'='*60}")
    print("SUCCESS! Add these to your .env file:")
    print(f"{'='*60}\n")
    print(f"TT_SECRET={client_secret}")
    print(f"TT_REFRESH={refresh_token}")
    print(f"\n{'='*60}")
    print("Refresh tokens typically don't expire. Keep it safe!")


if __name__ == "__main__":
    main()
