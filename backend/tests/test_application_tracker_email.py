"""Synthetic recruitment mail; all transports are injected fakes."""

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest

from app.application_tracker.email.extract import extract_event, parse_mail
from app.application_tracker.email.models import MailConfig, MailMessage
from app.application_tracker.email.store import MailStore
from app.application_tracker.models import ApplicationInput, ApplicationStatus
from app.application_tracker.store import ApplicationTrackerStore

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def mail(text="示例公司\n岗位：开发工程师\n邀请您参加笔试\n时间：2026-10-06 14:00", **kwargs):
    return MailMessage(provider="gmail", message_id="fixture-1", sender="jobs@example.com", subject="笔试通知", body=text, received_at=NOW, **kwargs)


def setup_store(tmp_path):
    tracker = ApplicationTrackerStore(tmp_path / "tracker.db")
    row = tracker.add_application("u", ApplicationInput(company="示例公司", role="开发工程师", url="https://jobs.example.com/applications"))
    return tracker, MailStore(tracker), row


def test_extract_keeps_event_time_separate_from_received_time(tmp_path):
    tracker, _, row = setup_store(tmp_path)
    event = extract_event(mail(), tracker.list_applications("u"))
    assert event.application_id == row.id
    assert event.kind == "written_test" and event.status == ApplicationStatus.WRITTEN_TEST
    assert event.quote in mail().body
    assert event.event_at.date().isoformat() == "2026-10-06"
    assert event.received_at == NOW


@pytest.mark.parametrize("text,kind,status", [("面试邀请", "interview", "面试（轮次待确认）"), ("录用通知", "offer", "Offer"), ("很遗憾，您未通过本次面试", "rejection", "未通过")])
def test_strict_events_do_not_invent_interview_rounds(tmp_path, text, kind, status):
    tracker, _, _ = setup_store(tmp_path)
    event = extract_event(mail(f"示例公司\n岗位：开发工程师\n{text}"), tracker.list_applications("u"))
    assert event.kind == kind and event.status == status and event.event_at is None


@pytest.mark.parametrize("text", ["示例公司 开发工程师 招聘资讯", "示例公司 开发工程师 不代表录用通知", "示例公司 开发工程师\n> 录用通知", "示例公司 开发工程师 笔试通知 面试邀请"])
def test_ambiguous_negated_and_quoted_events_require_review(tmp_path, text):
    tracker, _, _ = setup_store(tmp_path)
    assert extract_event(mail(text), tracker.list_applications("u")).review_reason


def test_cross_application_ambiguity_is_not_auto_linked(tmp_path):
    tracker, _, _ = setup_store(tmp_path)
    tracker.add_application("u", ApplicationInput(company="另一公司", role="测试工程师", url="https://other.example.com/a"))
    event = extract_event(mail("示例公司 开发工程师 另一公司 测试工程师 面试邀请"), tracker.list_applications("u"))
    assert event.application_id is None and event.review_reason == "ambiguous_application"


def test_store_is_idempotent_owner_scoped_and_rejects_forged_quotes(tmp_path):
    tracker, store, row = setup_store(tmp_path)
    message = mail()
    event = extract_event(message, tracker.list_applications("u"))
    assert store.save("u", message, event)
    assert not store.save("u", message, event)
    assert len(store.list_events("u")) == 1 and not store.list_events("other")
    with pytest.raises(ValueError):
        store.save("other", message, event)
    with pytest.raises(ValueError):
        store.save("u", message.model_copy(update={"message_id": "new"}), event.model_copy(update={"quote": "fabricated"}))
    tracker.delete_application("u", row.id)
    assert not store.list_events("u")


def test_merge_uses_receipt_time_not_future_appointment_and_keeps_portal(tmp_path):
    from app.application_tracker.models import CheckResult, StatusRecord

    tracker, store, row = setup_store(tmp_path)
    earlier = NOW - timedelta(days=1)
    tracker.save_check(
        "u",
        row.id,
        StatusRecord(company=row.company, role=row.role, url=row.url, status=ApplicationStatus.APPLIED, raw_status="已投递", evidence="已投递", confidence=1, checked_at=earlier, changed_at=earlier, check_result=CheckResult.SUCCESS),
    )
    store.save("u", mail(), extract_event(mail(), tracker.list_applications("u")))
    updated = tracker.get_application("u", row.id)
    assert updated.status is ApplicationStatus.APPLIED
    assert updated.source_summary["status"] == "笔试" and updated.source_summary["source"] == "email"
    assert not updated.source_summary["conflict"]


def test_terminal_conflict_never_silently_overwrites_portal(tmp_path):
    from app.application_tracker.models import CheckResult, StatusRecord

    tracker, store, row = setup_store(tmp_path)
    tracker.save_check(
        "u",
        row.id,
        StatusRecord(
            company=row.company,
            role=row.role,
            url=row.url,
            status=ApplicationStatus.OFFER,
            raw_status="Offer",
            evidence="Offer",
            confidence=1,
            checked_at=NOW - timedelta(days=1),
            changed_at=NOW - timedelta(days=1),
            check_result=CheckResult.SUCCESS,
        ),
    )
    message = mail("示例公司 开发工程师 很遗憾，您未通过本次面试")
    store.save("u", message, extract_event(message, tracker.list_applications("u")))
    result = tracker.get_application("u", row.id)
    assert result.source_summary["conflict"] and result.status is ApplicationStatus.OFFER


def test_mime_read_ignores_attachments_remote_images_and_quoted_history():
    msg = EmailMessage()
    msg["From"] = "招聘 <jobs@example.com>"
    msg["Subject"] = "笔试通知"
    msg.set_content("示例公司 开发工程师 笔试通知\n> 录用通知")
    msg.add_attachment(b"private", maintype="application", subtype="octet-stream", filename="resume.bin")
    parsed = parse_mail(msg.as_bytes(), provider="imap", message_id="one", received_at=NOW)
    assert "private" not in parsed.body and "录用通知" not in parsed.body


def test_defaults_off_and_exact_sender_domain():
    config = MailConfig(sender_domains=["example.com"])
    assert not config.enabled
    assert config.accepts("Jobs <jobs@example.com>", "面试邀请")
    assert not config.accepts("jobs@example.com.evil.test", "面试邀请")
    assert not config.accepts("jobs@example.com", "周末促销")


def test_disabled_sync_never_constructs_provider(monkeypatch, tmp_path):
    from app.application_tracker.email import service

    tracker, _, _ = setup_store(tmp_path)
    monkeypatch.setattr(service, "gmail_messages", lambda _: pytest.fail("Network must remain disabled"))
    assert service.sync_mail(tracker, "u", config=MailConfig())["inserted"] == 0


def test_production_http_reader_is_bounded_and_closes_response(monkeypatch):
    from io import BytesIO

    from app.application_tracker.email import providers

    body = BytesIO(b'{"messages": []}')

    class Opener:
        def open(self, request, timeout):
            assert request.method == "GET" and timeout == 20
            return body

    monkeypatch.setattr(providers, "build_opener", lambda *args: Opener())
    assert providers.bounded_json(None, "GET", "https://gmail.googleapis.com/fixture") == {"messages": []}
    assert body.closed


def test_gmail_fetches_only_filtered_bodies_and_uses_read_methods():
    import base64

    import httpx

    from app.application_tracker.email.providers import GMAIL_SCOPE, gmail_messages

    message = EmailMessage()
    message["From"] = "jobs@example.com"
    message["Subject"] = "面试邀请"
    message.set_content("示例公司 开发工程师 面试邀请")
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, request.url.params.get("format")))
        if request.url.path.endswith("messages"):
            return httpx.Response(200, json={"messages": [{"id": "good"}, {"id": "bad"}]})
        if request.url.params.get("format") == "metadata":
            sender = "jobs@example.com" if request.url.path.endswith("good") else "jobs@example.com.evil.test"
            return httpx.Response(200, json={"internalDate": str(int(NOW.timestamp() * 1000)), "sizeEstimate": 1000, "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "面试邀请"}]}})
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = list(gmail_messages(MailConfig(sender_domains=["example.com"], scope=GMAIL_SCOPE, access_token="fixture-only"), client=client, now=NOW))
    assert len(result) == 1 and result[0].received_at == NOW
    assert all(method == "GET" for method, _, _ in calls)
    assert not any(path.endswith("bad") and mode == "raw" for _, path, mode in calls)


def test_imap_uses_readonly_selection_and_peek_without_flag_mutation():
    from app.application_tracker.email.providers import imap_messages

    message = EmailMessage()
    message["From"] = "jobs@example.com"
    message["Subject"] = "面试邀请"
    message.set_content("示例公司 开发工程师 面试邀请")
    calls = []

    class Fake:
        def login(self, *args):
            pass

        def select(self, mailbox, readonly):
            assert readonly is True
            return "OK", []

        def response(self, name):
            return name, [b"17"]

        def uid(self, command, *args):
            calls.append((command, args))
            if command == "search":
                return "OK", [b"1"]
            if "HEADER.FIELDS" in args[-1]:
                return "OK", [(b'1 (RFC822.SIZE 400 INTERNALDATE "04-Oct-2026 00:00:00 +0000")', message.as_bytes())]
            return "OK", [(b"1", message.as_bytes())]

        def logout(self):
            calls.append(("logout", ()))

    config = MailConfig(provider="imap", sender_domains=["example.com"], imap_host="imap.example.com", imap_username="fixture", imap_password="fixture", imap_readonly_acknowledged=True)
    assert len(list(imap_messages(config, factory=lambda *a, **k: Fake(), now=NOW))) == 1
    assert all(command in {"search", "fetch", "logout"} for command, _ in calls)
    assert all("BODY.PEEK" in args[-1] for command, args in calls if command == "fetch")


def test_newer_generic_portal_and_older_specific_mail_preserve_conflict(tmp_path):
    from app.application_tracker.models import CheckResult, StatusRecord

    tracker, store, row = setup_store(tmp_path)
    store.save("u", mail(), extract_event(mail(), tracker.list_applications("u")))
    later = NOW + timedelta(hours=1)
    tracker.save_check(
        "u", row.id, StatusRecord(company=row.company, role=row.role, url=row.url, status=ApplicationStatus.APPLIED, confidence=1, raw_status="已投递", evidence="已投递", checked_at=later, changed_at=later, check_result=CheckResult.SUCCESS)
    )
    assert tracker.get_application("u", row.id).source_summary["conflict"]


@pytest.mark.asyncio
async def test_mail_routes_return_only_owner_events_and_no_credentials(monkeypatch, tmp_path):
    from app.application_tracker.email import service
    from app.gateway.routers import jobscout

    tracker, store, _ = setup_store(tmp_path)
    store.save("u", mail(), extract_event(mail(), tracker.list_applications("u")))
    monkeypatch.setattr(jobscout, "_tracker_store", lambda: tracker)
    monkeypatch.setattr(jobscout, "get_effective_user_id", lambda: "other")
    monkeypatch.setattr(service, "load_config", lambda _: MailConfig(access_token="private-fixture"))
    assert await jobscout.tracker_mail_events.__wrapped__(request=None) == []
    assert "private-fixture" not in str(await jobscout.tracker_mail_config.__wrapped__(request=None))
    assert (await jobscout.tracker_mail_sync.__wrapped__(request=None))["inserted"] == 0
