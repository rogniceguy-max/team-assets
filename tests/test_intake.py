import io
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from PIL import Image

from server import AssetService
from intake import IntakeService


def fixture_data(tmp_path):
    s = AssetService(tmp_path)
    p = s.state()["projects"][0]["id"]
    a = s.create_asset(p, "已有资产")
    v = s.create_variant(a["id"], "已有状态")
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(b, "PNG")
    m = s.add_materials(v["id"], [("a.png", b.getvalue())])["materials"][0]
    return s, p, a, v, m["file_id"]


def test_atomic_intake_rollback_and_retry(tmp_path):
    s, p, a, v, file_id = fixture_data(tmp_path)
    intake = IntakeService(s)
    before = s.state()
    with pytest.raises(HTTPException):
        intake.commit_intake(p, [{"name": "状态", "file_ids": [file_id]}, {"name": "失败", "file_ids": ["missing"]}], "failed", asset={"name": "新资产"})
    assert s.state() == before
    kwargs = dict(project_id=p, variants=[{"name": "战衣", "file_ids": [file_id], "material_names": {file_id: "战衣原图.png"}}], idempotency_key="one", asset={"name": "新资产"})
    result = intake.commit_intake(**kwargs)
    assert result["materials"][0]["name"] == "战衣原图.png"
    assert result["materials"][0]["asset_id"] == result["asset"]["id"]
    assert result["variants"][0]["final_material_id"] is None
    assert intake.commit_intake(**kwargs) == result
    with pytest.raises(HTTPException) as error:
        intake.commit_intake(**{**kwargs, "asset": {"name": "不同资产"}})
    assert error.value.status_code == 409
    assert len(s.state()["assets"]) == 2


def test_intake_concurrent_retry_and_wrong_parent(tmp_path):
    s, p, a, v, fid = fixture_data(tmp_path)
    kwargs = dict(project_id=p, asset_id=a["id"], variants=[{"variant_id": v["id"], "file_ids": [fid]}], idempotency_key="parallel")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: IntakeService(AssetService(tmp_path)).commit_intake(**kwargs), range(2)))
    assert results[0] == results[1]
    assert len(s.state()["materials"]) == 2
    other = s.create_asset(p, "其他资产")
    with pytest.raises(HTTPException):
        IntakeService(s).commit_intake(**{**kwargs, "asset_id": other["id"], "idempotency_key": "bad-parent"})


def test_intake_rejects_unknown_metadata(tmp_path):
    s, p, a, v, fid = fixture_data(tmp_path)
    for item in ({"name": " ", "file_ids": []}, {"name": "x", "file_ids": [fid, fid]}, {"variant_id": v["id"], "name": "wrong"}):
        with pytest.raises(HTTPException):
            IntakeService(s).commit_intake(p, [item], "bad", asset_id=a["id"])


def test_project_manifest_batches_file_and_candidate_lookups(tmp_path):
    s, project_id, asset, _, file_id = fixture_data(tmp_path)
    candidate = s.create_candidate_from_file(project_id, file_id, "candidate.png")
    other_project = s.create_project("另一个项目")
    foreign_candidate = s.create_candidate_from_file(other_project["id"], file_id, "foreign.png")
    statements = []
    connect = s.connect

    @contextmanager
    def traced_connect(write=False):
        with connect(write=write) as c:
            c.set_trace_callback(statements.append)
            yield c

    s.connect = traced_connect
    variants = [
        {
            "name": f"状态{i}",
            "file_ids": [file_id, "missing-file"],
            "candidate_ids": [candidate["id"], foreign_candidate["id"], "missing-candidate"],
        }
        for i in range(20)
    ]
    variants[1]["name"] = " "

    plan, errors, _ = IntakeService(s)._normalize_project_manifest(
        project_id, [{"asset_id": asset["id"], "variants": variants}]
    )

    assert plan is not None
    assert sum("FROM files WHERE id IN (" in sql for sql in statements) == 1
    assert sum("FROM candidates WHERE deleted_at IS NULL AND id IN (" in sql for sql in statements) == 1
    assert sum(error["code"] == "FILE_NOT_FOUND" for error in errors) == 20
    assert sum(error["code"] == "CANDIDATE_WRONG_PROJECT" for error in errors) == 20
    assert sum(error["code"] == "CANDIDATE_NOT_FOUND" for error in errors) == 20
    assert [error["code"] for error in errors[:4]] == [
        "FILE_NOT_FOUND", "CANDIDATE_WRONG_PROJECT", "CANDIDATE_NOT_FOUND", "INVALID_VARIANT_NAME",
    ]
