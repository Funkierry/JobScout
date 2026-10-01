#!/usr/bin/env python3
"""Interactively label private tracker snapshots without printing page contents."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
SNAPSHOT_ROOT = ROOT / "local_eval" / "tracker_snapshots"
sys.path.insert(0, str(BACKEND_DIR))

from app.application_tracker.annotation import case_from_snapshot, write_cases  # noqa: E402
from app.application_tracker.io import load_evaluation_cases  # noqa: E402
from app.application_tracker.models import ApplicationStatus  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", help="Only label one snapshot; default is all not yet labeled")
    return parser.parse_args()


def ask_required(prompt: str) -> str:
    while not (answer := input(prompt).strip()):
        print("此项不能为空。")
    return answer


def ask_status() -> ApplicationStatus:
    statuses = list(ApplicationStatus)
    print("状态选项：" + "  ".join(f"{index}. {status.value}" for index, status in enumerate(statuses, 1)))
    while True:
        answer = input("预期状态编号：").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(statuses):
            return statuses[int(answer) - 1]
        print("请输入上面列出的编号。")


def ask_date() -> date | None:
    while True:
        answer = input("页面可核实的投递日期 YYYY-MM-DD（无则回车）：").strip()
        if not answer:
            return None
        try:
            return date.fromisoformat(answer)
        except ValueError:
            print("请输入有效的 YYYY-MM-DD 日期。")


def main() -> int:
    args = parse_args()
    root = SNAPSHOT_ROOT.resolve()
    if not root.is_dir():
        raise SystemExit("尚无本地快照；请先运行 application_tracker_snapshot.py。")
    if args.snapshot_id:
        selected = (root / args.snapshot_id).resolve()
        if selected.parent != root or not selected.is_dir():
            raise SystemExit("快照编号无效。")
        snapshots = [selected]
    else:
        snapshots = sorted(path for path in root.iterdir() if path.is_dir() and (path / "metadata.json").is_file())

    labels_path = root / "labels.jsonl"
    cases = load_evaluation_cases(labels_path) if labels_path.exists() else []
    labeled_ids = {case.case_id for case in cases}
    for snapshot in snapshots:
        existing = [case_id for case_id in labeled_ids if case_id.startswith(snapshot.name + "-")]
        if existing and not args.snapshot_id:
            continue
        print(f"\n快照 {snapshot.name}：请自行查看其中的 body.txt 和 screenshot.png 后标注。")
        index = max((int(case_id.rsplit("-", 1)[-1]) for case_id in existing), default=0) + 1
        while True:
            case_id = f"{snapshot.name}-{index:02d}"
            company = ask_required("公司：")
            role = ask_required("待检查岗位（不确定可填“待识别岗位”）：")
            expected_status = ask_status()
            expected_role = input("页面明确对应的岗位原文（不确定则回车）：").strip() or None
            expected_applied_at = ask_date()
            case = case_from_snapshot(
                snapshot,
                case_id=case_id,
                company=company,
                role=role,
                expected_status=expected_status,
                expected_role=expected_role,
                expected_applied_at=expected_applied_at,
            )
            cases.append(case)
            labeled_ids.add(case_id)
            write_cases(labels_path, cases)
            if input("同一页面还有另一条已申请岗位吗？[y/N] ").strip().lower() != "y":
                break
            index += 1
    print(f"已保存 {len(cases)} 条标注：{labels_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Annotation failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
