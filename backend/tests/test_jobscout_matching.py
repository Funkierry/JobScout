from app.jobscout.inputs import BaseSnapshotStore
from app.jobscout.matching import score_matches

RECORDS = [{"record_id": "rec1", "公司": "示例", "岗位": "开发", "要求": "Python"}, {"record_id": "rec2", "公司": "示例二", "岗位": "工程师"}]
RESUMES = {"resume.md": "使用 Python 开发服务。"}


def item(**changes):
    return {"dimension": "skills", "points": 30, "resume_file": "resume.md", "resume_quote": "Python 开发", **changes}


def test_nonexistent_resume_fragment_scores_zero_and_total_is_recomputed():
    result = score_matches([{"record_id": "rec1", "total": 100, "score_items": [item(), item(dimension="projects", points=20, resume_quote="百万用户")]}], RECORDS, RESUMES)
    assert result[0]["total"] == 30
    rejected = next(item for item in result[0]["score_items"] if item["dimension"] == "projects")
    assert rejected["points"] == 0
    assert rejected["reason"] == "resume_quote_not_found"


def test_duplicate_invalid_points_and_fabricated_records_cannot_inflate_score():
    result = score_matches(
        [
            {"record_id": "invented", "score_items": [item()]},
            {"record_id": "rec1", "company": "伪造", "score_items": [item(), item(), item(dimension="projects", points=999), item(dimension="education", points=True)]},
            {"record_id": "rec2", "score_items": [item(points=20)]},
        ],
        RECORDS,
        RESUMES,
    )
    assert [row["record_id"] for row in result] == ["rec2", "rec1"]
    assert result[1]["total"] == 0
    assert result[1]["record"]["公司"] == "示例"


def test_no_uploaded_resume_no_points():
    assert score_matches([{"record_id": "rec1", "score_items": [item()]}], RECORDS, {})[0]["total"] == 0


def test_server_snapshots_are_bounded_immutable_owned_and_expire():
    now = [0]
    store = BaseSnapshotStore(max_entries=1, ttl_seconds=10, clock=lambda: now[0])
    ref = store.put("user", "thread", RECORDS)
    assert store.get(ref, "other", "thread") is None
    assert store.get(ref, "user", "other") is None
    copy = store.get(ref, "user", "thread")
    copy[0]["公司"] = "篡改"
    assert store.get(ref, "user", "thread")[0]["公司"] == "示例"
    store.put("user", "thread", RECORDS)
    assert store.get(ref, "user", "thread") is None
    now[0] = 11
    assert store.size == 0
