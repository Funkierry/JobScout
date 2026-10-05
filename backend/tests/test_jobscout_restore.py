"""Portable archive restore contracts on disposable directories."""

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


def archive_module():
    spec = importlib.util.spec_from_file_location("archive_cli", Path(__file__).resolve().parents[2] / "scripts/jobscout_archive.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(name="data/tracker.db", link=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, link
            archive.addfile(member)
        else:
            member.size = 7
            archive.addfile(member, io.BytesIO(b"fixture"))
    stream.seek(0)
    return stream


def test_restore_round_trip_and_refuse_nonempty_destination(tmp_path):
    module = archive_module()
    target = tmp_path / "volume"
    target.mkdir()
    module.restore(make_archive(), target)
    assert (target / "tracker.db").read_bytes() == b"fixture"
    with pytest.raises(ValueError, match="empty"):
        module.restore(make_archive(), target)
    assert (target / "tracker.db").read_bytes() == b"fixture"


def test_backup_can_actually_be_restored(tmp_path):
    module = archive_module()
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "account.db").write_bytes(b"synthetic db")
    (source / "SingletonLock").write_text("stale", encoding="utf-8")
    stream = io.BytesIO()
    module.backup(stream, source)
    stream.seek(0)
    module.restore(stream, target)
    assert (target / "account.db").read_bytes() == b"synthetic db"
    assert not (target / "SingletonLock").exists()


@pytest.mark.parametrize("name,link", [("data/../../outside", None), ("/data/absolute", None), ("other/file", None), ("data/link", "../../outside")])
def test_restore_rejects_escaping_archive_without_writes(tmp_path, name, link):
    target = tmp_path / "volume"
    target.mkdir()
    with pytest.raises((ValueError, tarfile.FilterError)):
        archive_module().restore(make_archive(name, link), target)
    assert list(target.iterdir()) == []
    assert not (tmp_path / "outside").exists()
