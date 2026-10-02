"""Run JobScout pytest with dotenv disabled and external sockets blocked.

Run from backend with its installed Python environment; never sync dependencies.
This is an in-process test guard, not an OS sandbox for arbitrary subprocesses.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--full", action="store_true", help="Attempt the upstream offline suite, stopping on its first failure")
    scope.add_argument("--blocking-io", action="store_true")
    parser.add_argument("--junitxml", default="../local_eval/jobscout_stage1/pytest.xml")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    os.environ.update(
        {
            "CI": "true",
            "PYTHON_DOTENV_DISABLED": "1",
            "DEER_FLOW_RUN_LIVE_TESTS": "0",
            "JOBSCOUT_REAL_RESEARCH": "0",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "OTEL_SDK_DISABLED": "true",
        }
    )
    for name in list(os.environ):
        upper = name.upper()
        if upper.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_ACCESS_KEY_ID")) or upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            os.environ.pop(name, None)
    # Some upstream modules load config at collection time. Give them an empty
    # fixture config rather than restoring a real key to satisfy local YAML.
    fixtures = backend.parent / "local_eval" / "jobscout_stage1"
    fixtures.mkdir(parents=True, exist_ok=True)
    config_path = fixtures / f"offline-config-{os.getpid()}.json"
    extensions_path = fixtures / f"offline-extensions-{os.getpid()}.json"
    config_path.write_text(json.dumps({"models": [], "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "extensions": {"middlewares": []}}), encoding="utf-8")
    extensions_path.write_text("{}", encoding="utf-8")
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(config_path)
    os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(extensions_path)
    blocked = 0

    def local_host(host) -> bool:
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="ignore")
        if host in (None, "", "localhost"):
            return True
        try:
            return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def audit(event, values):
        nonlocal blocked
        denied = False
        if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            denied = bool(values) and not local_host(values[0])
        elif event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            if getattr(values[0], "family", None) in (socket.AF_INET, socket.AF_INET6):
                address = values[-1]
                denied = not isinstance(address, tuple) or not local_host(address[0])
        if denied:
            blocked += 1
            raise PermissionError("JobScout offline tests blocked external networking")

    sys.addaudithook(audit)
    import pytest

    if args.full:
        tests = ["tests/", "-m", "not live", "--ignore=tests/blocking_io", "--ignore=tests/test_client_e2e.py", "-x"]
    elif args.blocking_io:
        tests = ["tests/blocking_io"]
    else:
        tests = sorted({str(path) for pattern in ("test_jobscout*.py", "test_application_tracker*.py", "test_configured_extensions.py", "test_harness_boundary.py") for path in Path("tests").glob(pattern)})
    print(json.dumps({"offline": True, "dotenv_disabled": True, "ci_mode": True, "scope": "full" if args.full else "blocking_io" if args.blocking_io else "jobscout"}), flush=True)
    exit_code = pytest.main([*tests, "-q", "--tb=short", f"--junitxml={args.junitxml}"])
    print(json.dumps({"exit_code": exit_code, "external_network_attempts_blocked": blocked}), flush=True)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
