"""Supervise Gateway and the optional SSH-only browser-login desktop."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def service_commands(browser_login: bool) -> list[list[str]]:
    commands = []
    if browser_login:
        commands.extend(
            [
                ["Xvfb", ":99", "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                ["x11vnc", "-display", ":99", "-localhost", "-rfbport", "5900", "-nopw", "-forever", "-shared", "-quiet"],
                ["websockify", "--web=/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900"],
            ]
        )
    commands.append([sys.executable, "-m", "uvicorn", "app.gateway.app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"])
    return commands


def main() -> int:
    enabled = os.environ.get("JOBSCOUT_BROWSER_LOGIN", "0").lower() in {"1", "true"}
    os.environ["APPLICATION_TRACKER_INTERACTIVE_LOGIN"] = "1" if enabled else "0"
    if enabled:
        os.environ["DISPLAY"] = ":99"
    children = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in service_commands(enabled):
            process = subprocess.Popen(command)
            children.append(process)
            if command[0] == "Xvfb":
                deadline = time.monotonic() + 10
                while not Path("/tmp/.X11-unix/X99").exists():
                    if stopping or process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Browser display did not become ready")
                    time.sleep(0.1)
        while not stopping:
            if any(process.poll() is not None for process in children):
                print("A JobScout service exited; stopping the container for a clean restart.", file=sys.stderr)
                return 1
            time.sleep(0.25)
        return 0
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 20
        for process in reversed(children):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
