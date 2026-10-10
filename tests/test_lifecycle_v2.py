import io
import sqlite3
import sys
from pathlib import Path

import pytest
from PIL import Image
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.utils import now, uid
from server import AssetService


def setup_tree(tmp_path):
    s = AssetService(tmp_path)
    p = s.list_projects()["projects"][0]
    a = s.create_asset(p["id"], "阿斯特莱亚")
    v = s.create_variant(a["id"], "战衣")
    body = io.BytesIO()
    Image.new("RGB", (20, 20)).save(body, "PNG")
    materials = s.add_materials(v["id"], [("a.png", body.getvalue()), ("b.png", body.getvalue())])["materials"]
    return s, p, a, v, materials


def test_cascade_preserves_prior_deletion_final_and_files(tmp_path):
    s, p, a, v, mats = setup_tree(tmp_path)
    s.delete_material(mats[0]["id"])
    s.update_variant(v["id"], 1, final_material_id=mats[1]["id"])
    revision = s.revision()
    preview = s.delete_entity("asset", a["id"], 1, dry_run=True)
    assert preview["affected"] == {"projects": 0, "assets": 1, "variants": 1, "materials": 1}
    assert s.revision() == revision
    deleted = s.delete_entity("asset", a["id"], 1)
    assert not s.state()["assets"] and not s.state()["materials"]
    assert len(s.trash()["materials"]) == 2
    with pytest.raises(HTTPException) as exc:
        s.restore_material(mats[1]["id"])
    assert exc.value.detail["code"] == "PARENT_DELETED"
    result = s.restore_entity("asset", a["id"])
    assert result["affected"] == preview["affected"]
    assert s.get_variant(v["id"])["final_material_id"] == mats[1]["id"]
    assert [m["id"] for m in s.state()["materials"]] == [mats[1]["id"]]
    assert len(list((tmp_path / "originals").iterdir())) == 1


def test_parent_restore_does_not_restore_previous_variant(tmp_path):
    s, p, a, v, mats = setup_tree(tmp_path)
    s.delete_entity("variant", v["id"], 1)
    s.delete_entity("project", p["id"], 1)
    s.restore_entity("project", p["id"])
    assert not s.state()["variants"]
    s.restore_entity("variant", v["id"])
    assert len(s.state()["materials"]) == 2


def test_restore_clears_final_when_conflicting_material_stays_deleted(tmp_path):
    s, p, a, v, _ = setup_tree(tmp_path)
    body = io.BytesIO()
    Image.new("RGB", (20, 20), "cyan").save(body, "PNG")
    candidate = s.add_candidate_uploads(
        p["id"], [("candidate.png", io.BytesIO(body.getvalue()), "image/png")]
    )["candidates"][0]
    final = s.create_candidate_reference(candidate["id"], a["id"])["material"]
    s.update_variant(v["id"], 1, final_material_id=final["id"])

    s.delete_entity("asset", a["id"], 1)
    with s.connect(write=True) as c:
        c.execute(
            "INSERT INTO materials(id,variant_id,name,source,file_id,created_at,deleted_at,delete_batch,asset_id,candidate_id) "
            "VALUES(?,NULL,?,'candidate',?,?,NULL,NULL,?,?)",
            (uid(), "replacement.png", candidate["file_id"], now(), a["id"], candidate["id"]),
        )

    s.restore_entity("asset", a["id"])
    assert s.get_variant(v["id"])["final_material_id"] is None


def test_deleted_targets_reject_writes_and_restart_stays_empty(tmp_path):
    s, p, a, v, mats = setup_tree(tmp_path)
    s.delete_entity("project", p["id"], 1)
    for action in (lambda: s.create_asset(p["id"], "bad"), lambda: s.update_asset(a["id"], 2, name="bad"), lambda: s.create_variant(a["id"], "bad")):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 409
    assert AssetService(tmp_path).state()["projects"] == []
    s.restore_entity("project", p["id"])
    assert len(s.state()["projects"]) == 1


def test_precise_queries_versions_and_clear_final(tmp_path):
    s, p, a, v, mats = setup_tree(tmp_path)
    assert s.search_assets("斯特", p["id"], "人物")["total"] == 1
    assert s.search_assets("missing")["total"] == 0
    assert s.get_asset(a["id"])["variants"][0]["path"].endswith("阿斯特莱亚 / 战衣")
    page = s.list_materials(v["id"], limit=1, offset=1)
    assert page["total"] == 2 and page["materials"][0]["width"] == 20
    assert page["materials"][0]["path"].endswith("阿斯特莱亚 / b.png")
    s.update_project(p["id"], 1, name="新项目")
    with pytest.raises(HTTPException) as exc:
        s.delete_entity("project", p["id"], 1)
    assert exc.value.detail["current"]["version"] == 2
    s.update_variant(v["id"], 1, final_material_id=mats[0]["id"])
    assert s.update_variant(v["id"], 2, final_material_id=None)["final_material_id"] is None


def test_legacy_empty_database_does_not_seed(tmp_path):
    (tmp_path / "database").mkdir()
    with sqlite3.connect(tmp_path / "database/assets.sqlite3") as c:
        c.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '')")
    s = AssetService(tmp_path)
    assert s.list_projects()["total"] == 0
    p = s.create_project("新")
    s.delete_entity("project", p["id"], 1)
    assert AssetService(tmp_path).list_projects()["total"] == 0
