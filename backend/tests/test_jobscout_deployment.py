"""Offline contracts for a fresh, isolated JobScout deployment."""

import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_cli():
    spec = importlib.util.spec_from_file_location("jobscout_deploy", ROOT / "scripts/jobscout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_init_preserves_existing_settings_and_generates_distinct_secrets(tmp_path, capsys):
    cli = load_cli()
    cli.initialize(tmp_path)
    env = cli.read_env(tmp_path / ".env")
    assert len(env["AUTH_JWT_SECRET"]) >= 32
    assert len({env[key] for key in ("AUTH_JWT_SECRET", "BETTER_AUTH_SECRET", "DEER_FLOW_INTERNAL_AUTH_TOKEN")}) == 3
    for key in ("AUTH_JWT_SECRET", "BETTER_AUTH_SECRET", "DEER_FLOW_INTERNAL_AUTH_TOKEN"):
        assert env[key] not in capsys.readouterr().out
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    (tmp_path / "config.yaml").write_text("operator-owned: true\n", encoding="utf-8")
    cli.initialize(tmp_path)
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "operator-owned: true\n"
    assert (tmp_path / ".env").read_bytes() == before[".env"]
    if os.name != "nt":
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


def test_deploy_defaults_are_persistent_authenticated_and_jobscout_bound(tmp_path):
    cli = load_cli()
    cli.initialize(tmp_path)
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["auth"]["local"]["allow_registration"] is False
    assert config["database"]["backend"] == "sqlite"
    assert config["database"]["sqlite_dir"].startswith("/data/")
    assert config["run_events"]["backend"] == "db"
    assert "app.jobscout.middleware:JobScoutLinkMiddleware" in config["extensions"]["middlewares"]
    assert config["scheduler"]["enabled"] is False
    compose = yaml.safe_load((ROOT / "docker/docker-compose.jobscout.yaml").read_text(encoding="utf-8"))
    gateway = compose["services"]["gateway"]
    assert gateway["environment"]["JOBSCOUT_ENFORCE_THREAD_BINDING"] == "1"
    assert gateway["environment"]["GATEWAY_WORKERS"] == "1"
    assert gateway["build"]["args"]["INSTALL_JOBSCOUT_BROWSER"] == "true"
    assert all(port.startswith("127.0.0.1:") for port in gateway["ports"])
    assert not compose["services"]["redis"].get("ports")
    assert not compose["services"]["frontend"].get("ports")


def test_doctor_rejects_missing_provider_settings_without_echoing_secrets(tmp_path):
    cli = load_cli()
    cli.initialize(tmp_path)
    errors = cli.configuration_errors(tmp_path)
    assert any("JOBSCOUT_MODEL_API_KEY" in error for error in errors)
    assert any("JOBSCOUT_MODEL_BASE_URL" in error for error in errors)
    env_path = tmp_path / ".env"
    text = env_path.read_text(encoding="utf-8").replace("your-model-id", "fixture-model").replace("replace-me", "private-test-value").replace("https://your-provider.example/v1", "https://provider.invalid/v1")
    env_path.write_text(text, encoding="utf-8")
    assert cli.configuration_errors(tmp_path) == []
    assert "private-test-value" not in " ".join(errors)


def test_compose_uses_explicit_env_and_project_directory(tmp_path):
    cli = load_cli()
    command = cli.compose_command(tmp_path, ["config", "--quiet"])
    assert command[:2] == ["docker", "compose"]
    assert command[command.index("--env-file") + 1] == str(tmp_path / ".env")
    assert command[-2:] == ["config", "--quiet"]


def test_deployment_config_passes_real_schema(tmp_path, monkeypatch):
    cli = load_cli()
    cli.initialize(tmp_path)
    for key, value in cli.read_env(tmp_path / ".env").items():
        monkeypatch.setenv(key, value)
    from deerflow.config.app_config import AppConfig

    config = AppConfig.from_file(str(tmp_path / "config.yaml"))
    assert config.models[0].name == "jobscout"


def test_runtime_config_contains_no_secrets(tmp_path):
    cli = load_cli()
    cli.initialize(tmp_path)
    public = (tmp_path / "runtime-config.js").read_text(encoding="utf-8")
    assert '"gatewayBase": ""' in public
    assert '"capabilityCenterUrl": "/workspace/capabilities"' in public
    assert "SECRET" not in public and "API_KEY" not in public


def test_failed_backup_restores_gateway_and_is_not_marked_complete(tmp_path, monkeypatch):
    cli = load_cli()
    calls = []

    def run(_directory, arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "run":
            kwargs["stdout"].write(b"incomplete fixture")
            raise subprocess.CalledProcessError(1, arguments)
        return SimpleNamespace(stdout="container-id\n", returncode=0)

    monkeypatch.setattr(cli, "compose", run)
    with pytest.raises(subprocess.CalledProcessError):
        cli.backup(tmp_path)
    assert ["stop", "gateway"] in calls and calls[-1] == ["start", "gateway"]
    assert not list((tmp_path / "backups").glob("*.tar.gz"))
    assert list((tmp_path / "backups").glob("*.partial"))


def test_failed_health_wait_never_prints_success(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    monkeypatch.setattr(cli.sys, "argv", ["jobscout.py", "up", "--directory", str(tmp_path)])
    monkeypatch.setattr(cli, "doctor", lambda _: True)
    calls = []

    def run(_directory, arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=1 if arguments[0] == "up" else 0)

    monkeypatch.setattr(cli, "compose", run)
    assert cli.main() == 1
    assert "JobScout is ready" not in capsys.readouterr().out
    assert any(args[0] == "logs" for args in calls)


def test_container_supervisor_stops_remaining_children_on_failure(monkeypatch):
    from scripts import jobscout_container as container

    monkeypatch.setenv("JOBSCOUT_BROWSER_LOGIN", "0")
    monkeypatch.setattr(container.signal, "signal", lambda *args: None)
    monkeypatch.setattr(container, "service_commands", lambda _: [["helper"], ["gateway"]])
    children = []

    class Child:
        def __init__(self, command):
            self.returncode = 3 if command == ["helper"] else None
            self.terminated = False
            children.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(container.subprocess, "Popen", Child)
    assert container.main() == 1
    assert children[1].terminated
