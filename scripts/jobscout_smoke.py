"""Check an ephemeral JobScout deployment without model or recruitment-site calls.

Only use on a disposable instance: creates a synthetic administrator and target.
"""

import argparse
import http.cookiejar
import json
import secrets
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:2026")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Smoke test is restricted to a disposable loopback instance")
    cookies = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))

    def request(path, data=None, form=False):
        headers = {"Origin": base}
        payload = None
        if data is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            payload = (urlencode(data) if form else json.dumps(data)).encode()
            csrf = next(
                (cookie.value for cookie in cookies if cookie.name == "csrf_token"),
                None,
            )
            if csrf:
                headers["X-CSRF-Token"] = csrf
        with opener.open(
            Request(base + path, data=payload, headers=headers), timeout=30
        ) as response:
            body = response.read().decode()
            return (
                json.loads(body)
                if "application/json" in response.headers.get("Content-Type", "")
                else body
            )

    assert "JobScout" in request("/")
    assert '"gatewayBase": ""' in request("/runtime-config.js")
    request("/health/ready")
    status = request("/api/v1/auth/setup-status")
    assert status["needs_setup"] and not status["registration_enabled"], (
        "Use a fresh disposable instance"
    )
    email, password = "smoke@example.test", secrets.token_urlsafe(24)
    user = request("/api/v1/auth/initialize", {"email": email, "password": password})
    assert user["system_role"] == "admin"
    assert request("/api/v1/auth/me")["email"] == email
    assert not request("/api/v1/auth/setup-status")["needs_setup"]
    try:
        request(
            "/api/v1/auth/register",
            {"email": "closed@example.test", "password": password},
        )
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("Registration should remain disabled")
    target = request(
        "/api/jobscout/opportunities",
        {"company": "Synthetic Company", "role": "Backend Engineer"},
    )
    assert target["company"] == "Synthetic Company"
    assert len(request("/api/jobscout/opportunities")) == 1
    assert request("/api/jobscout/tracker/applications") == []
    assert "jobscout" in json.dumps(request("/api/models"))
    assert "<html" in request("/workspace/capabilities").lower()
    request("/api/v1/auth/logout", {})
    request(
        "/api/v1/auth/login/local", {"username": email, "password": password}, form=True
    )
    assert request("/api/v1/auth/me")["email"] == email
    assert len(request("/api/jobscout/opportunities")) == 1
    print(
        "PASS: ingress, readiness, first admin, cookies/CSRF, closed registration, target storage, models and Capability Center. No model call."
    )


if __name__ == "__main__":
    main()
