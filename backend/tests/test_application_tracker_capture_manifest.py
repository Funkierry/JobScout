import json
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import application_tracker_capture_manifest as capture


def _private_manifest(tmp_path: Path, monkeypatch) -> Path:
    manifest = tmp_path / "url_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {"id": 1, "url": "https://example.com/applications/1", "category": "application"},
                    {"id": 2, "url": "https://example.org/applications/2", "category": "application"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(capture, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(capture, "PROGRESS_PATH", tmp_path / "capture_progress.json")
    return manifest


def test_manifest_capture_retries_login_and_resumes_failed_urls(tmp_path: Path, monkeypatch) -> None:
    _private_manifest(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["capture", "--user-id", "test-user", "--profile-dir", str(tmp_path / "profiles")])
    responses = iter([2, 0, 0, 1, 0])
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        commands.append(command)
        assert kwargs == {"cwd": capture.BACKEND_DIR, "check": False}
        assert command[-2:] == ["--profile-dir", str(tmp_path / "profiles")]
        return SimpleNamespace(returncode=next(responses))

    monkeypatch.setattr(capture.subprocess, "run", fake_run)

    assert capture.main() == 1
    assert json.loads(capture.PROGRESS_PATH.read_text(encoding="utf-8"))["completed_ids"] == [1]
    assert capture.main() == 0
    assert json.loads(capture.PROGRESS_PATH.read_text(encoding="utf-8"))["completed_ids"] == [1, 2]
    assert [Path(command[1]).name for command in commands] == [
        "application_tracker_snapshot.py",
        "application_tracker_browser_check.py",
        "application_tracker_snapshot.py",
        "application_tracker_snapshot.py",
        "application_tracker_snapshot.py",
    ]


def test_manifest_list_does_not_capture(tmp_path: Path, monkeypatch, capsys) -> None:
    _private_manifest(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["capture", "--list"])
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected capture")))

    assert capture.main() == 0
    assert "Private manifest contains 2 URLs." in capsys.readouterr().out
