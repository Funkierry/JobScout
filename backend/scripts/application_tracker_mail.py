#!/usr/bin/env python3
"""Configure private recruitment mail locally; network requires explicit commands."""

import argparse
import base64
import hashlib
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application_tracker.email.providers import GMAIL_SCOPE, bounded_json  # noqa: E402
from app.application_tracker.email.service import config_path, sync_mail, write_config  # noqa: E402
from app.application_tracker.store import ApplicationTrackerStore  # noqa: E402


def authorize_gmail(user_id, secrets_path):
    client = json.loads(secrets_path.read_text(encoding="utf-8"))["installed"]
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            if query.get("state", [None])[0] != state or not query.get("code"):
                self.send_error(400)
                return
            result["code"] = query["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this window.")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 180
        redirect = f"http://127.0.0.1:{server.server_port}/"
        webbrowser.open(
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": client["client_id"],
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": GMAIL_SCOPE,
                    "state": state,
                    "access_type": "offline",
                    "prompt": "consent",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        server.handle_request()
    if not result:
        raise ValueError("Authorization was not completed")
    grant = bounded_json(
        None,
        "POST",
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect, "client_id": client["client_id"], "client_secret": client["client_secret"], "code_verifier": verifier},
    )
    if grant.get("scope") != GMAIL_SCOPE:
        raise ValueError("Authorization must grant only gmail.readonly")
    values = {
        "provider": "gmail",
        "enabled": False,
        "sender_domains": [],
        "scope": GMAIL_SCOPE,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "access_token": grant["access_token"],
        "refresh_token": grant.get("refresh_token", ""),
    }
    write_config(user_id, values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "authorize-gmail", "sync"])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--client-secrets", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if args.command != "init" and not args.allow_network:
        raise ValueError("This command uses the network; add --allow-network explicitly")
    if args.command == "init":
        if config_path(args.user_id).exists():
            raise ValueError("Local configuration already exists; edit it locally")
        path = write_config(args.user_id, {"enabled": False, "provider": "gmail", "sender_domains": []})
        print(f"Edit local mail configuration: {path}")
    elif args.command == "authorize-gmail":
        if not args.client_secrets:
            raise ValueError("Provide a local installed-app OAuth client file")
        authorize_gmail(args.user_id, args.client_secrets)
        print("Gmail authorized; sync remains disabled until local configuration is completed.")
    else:
        print(json.dumps(sync_mail(ApplicationTrackerStore(), args.user_id)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Mail operation failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
