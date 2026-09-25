from __future__ import annotations

import pytest

from app.application_tracker.csv_import import CsvImportError, parse_application_csv


def test_csv_import_accepts_utf8_bom_and_product_columns() -> None:
    data = ("\ufeffcompany,role,url,applied_at,notes\r\nExample Co,AI PM,https://jobs.example.com/applications/1,2026-09-01,Campus\r\n").encode()

    rows = parse_application_csv(data)

    assert len(rows) == 1
    assert rows[0].company == "Example Co"
    assert rows[0].applied_at is not None
    assert rows[0].applied_at.isoformat() == "2026-09-01"


def test_csv_import_rejects_missing_required_column() -> None:
    data = b"company,role,url,applied_at\nExample,PM,https://example.com/1,2026-09-01\n"

    with pytest.raises(CsvImportError, match="notes"):
        parse_application_csv(data)


def test_csv_import_reports_invalid_row_without_reading_network() -> None:
    data = b"company,role,url,applied_at,notes\nExample,PM,javascript:alert(1),2026-09-01,\n"

    with pytest.raises(CsvImportError, match="2"):
        parse_application_csv(data)


def test_csv_import_enforces_file_and_row_limits() -> None:
    with pytest.raises(CsvImportError, match="size"):
        parse_application_csv(b"x" * 20, max_bytes=10)

    data = b"company,role,url,applied_at,notes\nA,PM,https://example.com/1,,\nB,PM,https://example.com/2,,\n"
    with pytest.raises(CsvImportError, match="rows"):
        parse_application_csv(data, max_rows=1)
