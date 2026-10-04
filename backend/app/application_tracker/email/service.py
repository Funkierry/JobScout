"""Local configuration and bounded, explicit synchronization."""

import json
from hashlib import sha256
from pathlib import Path

from app.application_tracker.email.extract import extract_event
from app.application_tracker.email.models import MailConfig
from app.application_tracker.email.providers import gmail_messages, imap_messages
from app.application_tracker.email.store import MailStore
from deerflow.config.paths import get_paths


def config_path(user_id: str) -> Path:
    identity = sha256(user_id.encode()).hexdigest()
    return get_paths().base_dir / "jobscout-mail" / identity / "config.json"


def load_config(user_id: str) -> MailConfig:
    path = config_path(user_id)
    if not path.exists():
        return MailConfig()
    if path.stat().st_size > 32_000:
        raise ValueError("Local mail config exceeds size limit")
    return MailConfig.model_validate_json(path.read_text(encoding="utf-8"))


def write_config(user_id: str, values: dict) -> Path:
    MailConfig.model_validate(values)
    path = config_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def sync_mail(tracker, user_id: str, *, config: MailConfig | None = None, messages=None) -> dict:
    config = config or load_config(user_id)
    if not config.enabled or not config.sender_domains:
        return {"enabled": config.enabled, "inserted": 0, "review": 0, "duplicate": 0, "reason": "disabled_or_empty_allowlist"}
    if messages is None:
        messages = gmail_messages(config) if config.provider == "gmail" else imap_messages(config)
    store = MailStore(tracker)
    applications = tracker.list_applications(user_id)
    inserted = review = duplicate = scanned = 0
    try:
        for message in messages:
            scanned += 1
            if scanned > config.max_messages:
                break
            if not config.accepts(message.sender, message.subject):
                continue
            event = extract_event(message, applications, timezone=config.timezone)
            if store.save(user_id, message, event):
                inserted += 1
                review += bool(event.review_reason)
            else:
                duplicate += 1
    finally:
        close = getattr(messages, "close", None)
        if close:
            close()
    return {"enabled": True, "inserted": inserted, "review": review, "duplicate": duplicate}
