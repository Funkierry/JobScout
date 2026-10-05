"""Cross-platform JobScout deployment CLI. Requires Python 3.10+ and Docker Compose v2."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / ".jobscout"


def write_new(path: Path, text: str, mode: int = 0o600) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def initialize(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    template = (ROOT / "deploy/jobscout/env.example").read_text(encoding="utf-8")
    for key in (
        "AUTH_JWT_SECRET",
        "BETTER_AUTH_SECRET",
        "DEER_FLOW_INTERNAL_AUTH_TOKEN",
    ):
        template = template.replace(
            f"{key}=__GENERATE__", f"{key}={secrets.token_urlsafe(48)}"
        )
    write_new(directory / ".env", template)
    write_new(
        directory / "config.yaml",
        (ROOT / "deploy/jobscout/config.example.yaml").read_text(encoding="utf-8"),
    )
    write_new(
        directory / "extensions_config.json",
        json.dumps({"mcpServers": {}, "skills": {}}, indent=2) + "\n",
    )
    write_new(
        directory / "runtime-config.js",
        "window.JOBSCOUT_CONFIG = "
        + json.dumps(
            {"gatewayBase": "", "capabilityCenterUrl": "/workspace/capabilities"}
        )
        + ";\n",
        mode=0o644,
    )
    print(
        f"Deployment files ready: {directory}\nExisting files were preserved. Set the three JOBSCOUT_MODEL values in .env, then run doctor."
    )


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def configuration_errors(directory: Path) -> list[str]:
    errors = []
    for name in (".env", "config.yaml", "extensions_config.json", "runtime-config.js"):
        if not (directory / name).is_file():
            errors.append(f"Missing {name}; run init first")
    if errors:
        return errors
    values = read_env(directory / ".env")
    mode = values.get("JOBSCOUT_DEPLOY_MODE", "source")
    if mode not in {"source", "images"}:
        errors.append("JOBSCOUT_DEPLOY_MODE must be source or images")
    if mode == "images":
        if not re.fullmatch(r"sha-[0-9a-f]{40}", values.get("JOBSCOUT_IMAGE_TAG", "")):
            errors.append(
                "JOBSCOUT_IMAGE_TAG must be sha- followed by the full 40-character commit hash"
            )
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9./_-]*", values.get("JOBSCOUT_IMAGE_PREFIX", "")
        ):
            errors.append(
                "Set JOBSCOUT_IMAGE_PREFIX to a lowercase registry/repository prefix"
            )
    for key in ("JOBSCOUT_MODEL", "JOBSCOUT_MODEL_API_KEY", "JOBSCOUT_MODEL_BASE_URL"):
        if values.get(key, "") in {
            "",
            "your-model-id",
            "replace-me",
            "https://your-provider.example/v1",
        }:
            errors.append(f"Set {key} in the deployment .env")
    parsed = urlsplit(values.get("JOBSCOUT_MODEL_BASE_URL", ""))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        errors.append(
            "JOBSCOUT_MODEL_BASE_URL must be an HTTP(S) URL without credentials"
        )
    for key in (
        "AUTH_JWT_SECRET",
        "BETTER_AUTH_SECRET",
        "DEER_FLOW_INTERNAL_AUTH_TOKEN",
    ):
        if len(values.get(key, "")) < 32:
            errors.append(f"Set a stable random {key} of at least 32 characters")
    for key, default in (("JOBSCOUT_PORT", "2026"), ("JOBSCOUT_BROWSER_PORT", "6080")):
        value = values.get(key, default)
        if not value.isdecimal() or not 1 <= int(value) <= 65535:
            errors.append(f"{key} must be a port between 1 and 65535")
    return errors


def compose_command(directory: Path, arguments: list[str]) -> list[str]:
    values = read_env(directory / ".env") if (directory / ".env").is_file() else {}
    overrides = []
    if values.get("JOBSCOUT_DEPLOY_MODE") == "images":
        overrides = ["-f", str(ROOT / "docker/docker-compose.jobscout-images.yaml")]
    return [
        "docker",
        "compose",
        "--project-name",
        "jobscout",
        "--project-directory",
        str(ROOT / "docker"),
        "--env-file",
        str(directory / ".env"),
        "-f",
        str(ROOT / "docker/docker-compose.jobscout.yaml"),
        *overrides,
        *arguments,
    ]


def compose(
    directory: Path, arguments: list[str], **kwargs
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # The deployment env file is authoritative; don't inherit a different
    # checkout's model/secrets/port overrides through the host process.
    for key in read_env(directory / ".env"):
        env.pop(key, None)
    env["JOBSCOUT_DEPLOY_DIR"] = directory.as_posix()
    return subprocess.run(
        compose_command(directory, arguments), cwd=ROOT, env=env, **kwargs
    )


def doctor(directory: Path) -> bool:
    errors = configuration_errors(directory)
    if not shutil.which("docker"):
        errors.append(
            "Docker was not found; install Docker Engine/Desktop with Compose v2"
        )
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return False
    try:
        version = subprocess.run(
            ["docker", "compose", "version", "--short"],
            check=True,
            timeout=15,
            capture_output=True,
            text=True,
        )
        if read_env(directory / ".env").get("JOBSCOUT_DEPLOY_MODE") == "images":
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
            if not match or tuple(map(int, match.groups())) < (2, 24, 4):
                print(
                    "ERROR: Image deployment requires Docker Compose 2.24.4 or newer.",
                    file=sys.stderr,
                )
                return False
        engine = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            check=True,
            timeout=15,
            capture_output=True,
            text=True,
        )
        if engine.stdout.strip() != "linux":
            print("ERROR: Switch Docker to Linux containers.", file=sys.stderr)
            return False
        compose(directory, ["config", "--quiet"], check=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        print(
            "ERROR: Docker Engine / Compose validation failed. Start Docker and use Linux containers.",
            file=sys.stderr,
        )
        return False
    print(
        "PASS: configuration and Docker Compose are ready. No model request was sent."
    )
    return True


def backup(directory: Path) -> None:
    target = (
        directory
        / "backups"
        / datetime.now(timezone.utc).strftime("jobscout-%Y%m%dT%H%M%SZ.tar.gz")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    # Stop the only SQLite writer; preserve an intentionally stopped service.
    status = compose(
        directory,
        ["ps", "--status", "running", "-q", "gateway"],
        check=True,
        capture_output=True,
        text=True,
    )
    was_running = bool(status.stdout.strip())
    if was_running:
        compose(directory, ["stop", "gateway"], check=True)
    try:
        code = (ROOT / "scripts/jobscout_archive.py").read_text(encoding="utf-8")
        partial = target.with_suffix(target.suffix + ".partial")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            compose(
                directory,
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "/app/backend/.venv/bin/python",
                    "gateway",
                    "-c",
                    code,
                    "backup",
                ],
                check=True,
                stdout=stream,
            )
        partial.replace(target)
        print(
            f"Data backup saved: {target}. Keep the deployment .env and config files separately."
        )
    finally:
        if was_running:
            compose(directory, ["start", "gateway"], check=True)


def restore(directory: Path, archive: Path) -> None:
    status = compose(
        directory,
        ["ps", "--status", "running", "-q", "gateway"],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise ValueError(
            "Stop the deployment before restoring to a new empty data volume"
        )
    code = (ROOT / "scripts/jobscout_archive.py").read_text(encoding="utf-8")
    with archive.open("rb") as stream:
        compose(
            directory,
            [
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "/app/backend/.venv/bin/python",
                "gateway",
                "-c",
                code,
                "restore",
            ],
            stdin=stream,
            check=True,
        )
    print(
        "Data restored. Run up with the original deployment configuration and secrets."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("init", "doctor", "up", "down", "status", "logs", "backup", "restore"),
    )
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument(
        "--no-pull",
        action="store_true",
        help="Use already-loaded images in image mode (offline/CI)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="Backup .tar.gz to restore into an empty data volume",
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    try:
        if args.command == "init":
            initialize(directory)
            return 0
        if args.command == "doctor":
            return 0 if doctor(directory) else 1
        if args.command == "up":
            if not doctor(directory):
                return 1
            values = (
                read_env(directory / ".env") if (directory / ".env").is_file() else {}
            )
            image_mode = values.get("JOBSCOUT_DEPLOY_MODE") == "images"
            if image_mode and not args.no_pull:
                compose(directory, ["pull"], check=True)
            result = compose(
                directory,
                [
                    "up",
                    "--no-build" if image_mode else "--build",
                    *(["--pull", "never"] if image_mode else []),
                    "--wait",
                    "--wait-timeout",
                    "240",
                ],
            )
            if result.returncode:
                compose(directory, ["ps"])
                compose(directory, ["logs", "--tail", "60", "gateway", "nginx"])
                return result.returncode
            values = read_env(directory / ".env")
            print(
                f"JobScout is ready: http://localhost:{values.get('JOBSCOUT_PORT', '2026')}\nCreate your administrator account before exposing this instance."
            )
            if values.get("JOBSCOUT_BROWSER_LOGIN") in {"1", "true"}:
                print(
                    f"Private browser: http://localhost:{values.get('JOBSCOUT_BROWSER_PORT', '6080')}/vnc.html (SSH tunnel for remote hosts)"
                )
            return 0
        if not (directory / ".env").is_file():
            parser.error("Run init first")
        if args.command == "backup":
            backup(directory)
            return 0
        if args.command == "restore":
            if args.archive is None:
                parser.error("restore requires --archive")
            restore(directory, args.archive.resolve())
            return 0
        arguments = {
            "down": ["down"],
            "status": ["ps"],
            "logs": ["logs", "--tail", "100", "-f"],
        }[args.command]
        return compose(directory, arguments).returncode
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"Deployment operation failed: {type(exc).__name__}. Check Docker and the deployment files.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
