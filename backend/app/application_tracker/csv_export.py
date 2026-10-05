"""Spreadsheet-friendly CSV with a versioned, reversible text encoding."""

import csv
import io

ENCODING_VERSION = "jobscout-text-v1"


def text_cell(value):
    text = str(value) if value is not None else ""
    if text.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")) or text.startswith(("\t", "\r", "\n")):
        return "\t" + text
    return text


def export_applications(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(["公司", "岗位", "投递日期", "投递日期原文", "自定义环节", "识别状态", "页面原始状态", "查询链接", "识别结果", "最近检查时间", "置信度", "证据", "备注", "jobscout_csv_version"])
    for row in rows:
        values = [
            row.company,
            row.role,
            row.applied_at,
            row.applied_at_evidence,
            row.stage,
            row.status.value,
            row.raw_status,
            row.url,
            row.check_result.value if row.check_result else "",
            row.checked_at.isoformat() if row.checked_at else "",
            row.confidence,
            row.evidence,
            row.notes,
            ENCODING_VERSION,
        ]
        writer.writerow([text_cell(value) for value in values])
    return "\ufeff" + output.getvalue()
