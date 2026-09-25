"""Typed browser contracts and environment-backed configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.application_tracker.models import CheckResult
from deerflow.config.paths import get_paths, resolve_path


class LoginState(StrEnum):
    """Rule-based authentication state of the currently visible page."""

    AUTHENTICATED = "authenticated"
    LOGIN_REQUIRED = "login_required"
    HUMAN_CHALLENGE = "human_challenge"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LoginInspection:
    state: LoginState
    reason: str


class BrowserEventType(StrEnum):
    HUMAN_LOGIN_REQUIRED = "human_login_required"
    HUMAN_CHALLENGE = "human_challenge"
    LOGIN_COMPLETED = "login_completed"
    LOGIN_TIMED_OUT = "login_timed_out"


@dataclass(frozen=True, slots=True)
class BrowserEvent:
    """A safe progress event; it deliberately contains no cookies or page text."""

    type: BrowserEventType
    message: str


@dataclass(frozen=True, slots=True)
class BrowserAccessResult:
    page_text: str
    check_result: CheckResult
    login_state: LoginState
    login_attempted: bool = False
    error_code: str | None = None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class BrowserAccessConfig:
    """Settings for one persistent browser access operation."""

    profile_root: Path
    headless: bool = True
    login_timeout_seconds: float = 300.0
    login_poll_interval_seconds: float = 1.5
    navigation_timeout_ms: int = 30_000
    page_text_timeout_ms: int = 10_000
    allow_private_addresses: bool = False

    def __post_init__(self) -> None:
        if self.login_timeout_seconds < 0:
            raise ValueError("login_timeout_seconds must be non-negative")
        if self.login_poll_interval_seconds <= 0:
            raise ValueError("login_poll_interval_seconds must be positive")
        if self.navigation_timeout_ms <= 0 or self.page_text_timeout_ms <= 0:
            raise ValueError("browser timeouts must be positive")

    @classmethod
    def from_env(cls) -> BrowserAccessConfig:
        default_root = get_paths().base_dir / "application-tracker" / "browser_profile"
        configured_root = os.getenv("APPLICATION_TRACKER_BROWSER_PROFILE_DIR")
        profile_root = resolve_path(configured_root) if configured_root else default_root
        return cls(
            profile_root=profile_root,
            headless=_env_bool("APPLICATION_TRACKER_BROWSER_HEADLESS", True),
            login_timeout_seconds=_env_float("APPLICATION_TRACKER_LOGIN_TIMEOUT_SECONDS", 300.0),
            login_poll_interval_seconds=_env_float("APPLICATION_TRACKER_LOGIN_POLL_SECONDS", 1.5),
            navigation_timeout_ms=_env_int("APPLICATION_TRACKER_NAVIGATION_TIMEOUT_MS", 30_000),
            page_text_timeout_ms=_env_int("APPLICATION_TRACKER_PAGE_TEXT_TIMEOUT_MS", 10_000),
        )
