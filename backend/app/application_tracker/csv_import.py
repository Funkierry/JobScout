"""Strict, offline parsing for the application tracker source CSV."""

from __future__ import annotations

import csv
import io

from pydantic import ValidationError

from app.application_tracker.models import ApplicationInput

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ROWS = 2_000
REQUIRED_COLUMNS = {"company", "role", "url", "applied_at", "notes"}


class CsvImportError(ValueError):
    """A user-correctable CSV validation error."""


def parse_application_csv(
    data: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[ApplicationInput]:
    """Parse CSV bytes without performing any network access."""

    if len(data) > max_bytes:
        raise CsvImportError(f"CSV exceeds the {max_bytes}-byte size limit")
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise CsvImportError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise CsvImportError(f"CSV is missing required columns: {', '.join(missing)}")

    applications: list[ApplicationInput] = []
    for row_number, row in enumerate(reader, start=2):
        values = {name: (row.get(name) or "").strip() for name in REQUIRED_COLUMNS}
        if not any(values.values()):
            continue
        if len(applications) >= max_rows:
            raise CsvImportError(f"CSV exceeds the {max_rows}-rows limit")
        payload: dict[str, str | None] = dict(values)
        payload["applied_at"] = values["applied_at"] or None
        try:
            applications.append(ApplicationInput.model_validate(payload))
        except ValidationError as exc:
            raise CsvImportError(f"Invalid CSV row {row_number}: {exc}") from exc
    return applications
