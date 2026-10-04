"""Read-only transports. Credentials and protocol errors are never logged."""

import base64
import imaplib
import json
import re
import ssl
from contextlib import closing
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.application_tracker.email.extract import parse_mail
from app.application_tracker.email.models import MailConfig

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_BYTES = 1_000_000


def bounded_json(client, method: str, url: str, **kwargs):
    if client is None:
        # urllib has no request INFO log containing private message IDs or queries.
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        headers = dict(kwargs.get("headers", {}))
        if kwargs.get("params"):
            url += "?" + urlencode(kwargs["params"], doseq=True)
        data = None
        if kwargs.get("data"):
            data = urlencode(kwargs["data"]).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        with closing(build_opener(ProxyHandler({}), NoRedirect()).open(Request(url, data=data, headers=headers, method=method), timeout=20)) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("Provider response exceeds limit")
            return json.loads(body)
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise ValueError("Provider response exceeds limit")
        return json.loads(body)


def gmail_messages(config: MailConfig, *, client=None, now: datetime | None = None):
    if config.scope != GMAIL_SCOPE:
        raise ValueError("Gmail requires the readonly OAuth grant")
    token = config.access_token
    if config.refresh_token:
        grant = bounded_json(client, "POST", "https://oauth2.googleapis.com/token", data={"grant_type": "refresh_token", "refresh_token": config.refresh_token, "client_id": config.client_id, "client_secret": config.client_secret})
        if grant.get("scope", GMAIL_SCOPE) != GMAIL_SCOPE:
            raise ValueError("Unexpected Gmail OAuth scope")
        token = grant["access_token"]
    if not token:
        raise ValueError("Missing local Gmail authorization")
    headers = {"Authorization": f"Bearer {token}"}
    since = (now or datetime.now(UTC)) - timedelta(days=config.lookback_days)
    domains = " ".join(f"from:(@{domain})" for domain in config.sender_domains)
    keywords = " ".join(f'subject:"{word}"' for word in config.keywords)
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    listing = bounded_json(client, "GET", url, headers=headers, params={"q": f"after:{int(since.timestamp())} {{{domains}}} {{{keywords}}}", "maxResults": config.max_messages, "includeSpamTrash": "false"})
    for entry in listing.get("messages", [])[: config.max_messages]:
        message_id = str(entry["id"])
        endpoint = url + "/" + quote(message_id, safe="")
        metadata = bounded_json(client, "GET", endpoint, headers=headers, params={"format": "metadata", "metadataHeaders": ["From", "Subject"]})
        fields = {item["name"].lower(): item["value"] for item in metadata.get("payload", {}).get("headers", [])}
        if not config.accepts(fields.get("from", ""), fields.get("subject", "")) or int(metadata.get("sizeEstimate", MAX_BYTES + 1)) > MAX_BYTES:
            continue
        received = datetime.fromtimestamp(int(metadata["internalDate"]) / 1000, tz=UTC)
        if received < since or received > (now or datetime.now(UTC)) + timedelta(minutes=5):
            continue
        payload = bounded_json(client, "GET", endpoint, headers=headers, params={"format": "raw"})
        raw = base64.urlsafe_b64decode(payload["raw"] + "===")
        message = parse_mail(raw, provider="gmail", message_id=message_id, received_at=received)
        if config.accepts(message.sender, message.subject):
            yield message


def imap_messages(config: MailConfig, *, factory=None, now: datetime | None = None):
    if not config.imap_readonly_acknowledged or not config.imap_host or not config.imap_username or not config.imap_password:
        raise ValueError("IMAP needs local authorization and readonly acknowledgement")
    connect = factory or imaplib.IMAP4_SSL
    client = connect(config.imap_host, 993, ssl_context=ssl.create_default_context(), timeout=20)
    try:
        client.login(config.imap_username, config.imap_password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Cannot open readonly inbox")
        validity = client.response("UIDVALIDITY")[1][0].decode("ascii")
        since = (now or datetime.now(UTC)) - timedelta(days=config.lookback_days)
        uids = set()
        for domain in config.sender_domains:
            status, data = client.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", f'"@{domain}"')
            if status != "OK":
                raise ValueError("Mail search failed")
            # Headers only; body is fetched only after exact domain + subject filtering.
            uids.update(uid for uid in data[0].split() if uid.isdigit())
        for uid in sorted(uids, key=int, reverse=True)[: config.max_messages]:
            status, data = client.uid("fetch", uid, "(RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK":
                continue
            parts = [part for part in data if isinstance(part, tuple)]
            if not parts:
                continue
            descriptor, header = parts[0]
            size = re.search(rb"RFC822.SIZE (\d+)", descriptor)
            internal = re.search(rb'INTERNALDATE "([^"]+)"', descriptor)
            if not size or int(size[1]) > MAX_BYTES or not internal:
                continue
            received = datetime.strptime(internal[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").astimezone(UTC)
            parsed = BytesParser(policy=policy.default).parsebytes(header)
            if received < since or received > (now or datetime.now(UTC)) + timedelta(minutes=5) or not config.accepts(str(parsed.get("From", "")), str(parsed.get("Subject", ""))):
                continue
            status, data = client.uid("fetch", uid, f"(BODY.PEEK[]<0.{MAX_BYTES + 1}>)")
            if status != "OK":
                continue
            raw = next((part[1] for part in data if isinstance(part, tuple)), b"")
            if len(raw) > MAX_BYTES:
                continue
            message = parse_mail(raw, provider="imap", message_id=f"{validity}:{uid.decode('ascii')}", received_at=received)
            if config.accepts(message.sender, message.subject):
                yield message
    finally:
        # Do not call CLOSE/EXPUNGE, STORE, COPY, MOVE or send mail.
        try:
            client.logout()
        except Exception:
            pass
