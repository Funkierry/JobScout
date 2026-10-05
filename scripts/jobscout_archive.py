"""Data-volume archive helper, executed with Python 3.12 inside the container."""

from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile


def backup(stream, source=Path("/data")):
    def persistent(member):
        # Chromium recreates these host-specific lock/socket symlinks on launch.
        if PurePosixPath(member.name).name in {
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
        }:
            return None
        if not (member.isfile() or member.isdir()):
            raise ValueError("Backup supports regular files and directories only")
        return member

    with tarfile.open(fileobj=stream, mode="w|gz") as archive:
        archive.add(source, arcname="data", filter=persistent)


def restore(stream, destination=Path("/data")):
    if any(destination.iterdir()):
        raise ValueError("Restore requires an empty data volume")
    # Buffer to disk so all paths are validated before any extracted content is
    # placed in the volume; stdin may be a non-seekable Docker pipe.
    with tempfile.TemporaryFile() as buffered:
        shutil.copyfileobj(stream, buffered)
        buffered.seek(0)
        with tarfile.open(fileobj=buffered, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("Empty backup archive")
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] != "data"
                    or "\\" in member.name
                ):
                    raise ValueError("Backup contains an invalid data path")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(
                        "Backup contains unsupported links or special files"
                    )
            with tempfile.TemporaryDirectory(dir=destination) as staging:
                archive.extractall(staging, members=members, filter="data")
                restored = Path(staging) / "data"
                if not restored.is_dir():
                    raise ValueError("Backup must contain the data directory")
                for item in restored.iterdir():
                    shutil.move(str(item), destination / item.name)


if __name__ == "__main__":
    if sys.argv[1] == "backup":
        backup(sys.stdout.buffer)
    elif sys.argv[1] == "restore":
        restore(sys.stdin.buffer)
    else:
        raise SystemExit("Expected backup or restore")
