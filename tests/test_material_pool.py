"""Asset ownership survives variant lifecycle and supports shared final refs."""
import sqlite3
import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from server import AssetService, create_app
from test_api import png
from test_lifecycle_v2 import setup_tree


def test_shared_final_and_source_variant_deletion(tmp_path):
    s, _, a, v, mats = setup_tree(tmp_path)
    other = s.create_variant(a["id"], "常服")
    first = s.update_variant(v["id"], 1, final_material_id=mats[0]["id"])
    second = s.update_variant(other["id"], 1, final_material_id=mats[0]["id"])
    assert s.list_materials(other["id"])["total"] == 2
    preview = s.delete_entity("variant", v["id"], first["version"], dry_run=True)
    assert preview["affected"]["materials"] == 0
    s.delete_entity("variant", v["id"], first["version"])
    assert s.get_variant(other["id"])["final_material_id"] == mats[0]["id"]
    assert s.get_variant(other["id"])["version"] == second["version"]
    assert len(s.list_materials(asset_id=a["id"])["materials"]) == 2
    # Materials no longer require their deleted provenance variant to exist.
    s.delete_material(mats[0]["id"])
    assert s.get_variant(other["id"])["final_material_id"] is None
    assert s.get_variant(other["id"])["version"] == second["version"] + 1
    s.restore_material(mats[0]["id"])
    assert s.get_variant(other["id"])["final_material_id"] is None
    restored = s.restore_entity("variant", v["id"])["entity"]
    assert restored["final_material_id"] is None


def test_replacing_one_shared_final_is_independent_and_cross_asset_rejected(tmp_path):
    s, p, a, v, mats = setup_tree(tmp_path)
    second = s.create_variant(a["id"], "另一状态")
    s.update_variant(v["id"], 1, final_material_id=mats[0]["id"])
    s.update_variant(second["id"], 1, final_material_id=mats[0]["id"])
    s.update_variant(v["id"], 2, final_material_id=mats[1]["id"])
    assert s.get_variant(second["id"])["final_material_id"] == mats[0]["id"]
    b = s.create_asset(p["id"], "其他资产")
    outsider = s.create_variant(b["id"], "状态")
    revision = s.revision()
    with pytest.raises(HTTPException) as exc:
        s.update_variant(outsider["id"], 1, final_material_id=mats[0]["id"])
    assert exc.value.status_code == 422
    assert s.revision() == revision
    s.delete_material(mats[0]["id"])
    with pytest.raises(HTTPException) as exc:
        s.update_variant(v["id"], 3, final_material_id=mats[0]["id"])
    assert exc.value.status_code == 409


def test_asset_upload_without_variants_http_and_retry(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        p = c.get("/api/state").json()["projects"][0]
        a = c.post("/api/assets", json={"project_id": p["id"], "name": "空资产"}).json()
        url = f'/api/assets/{a["id"]}/materials'
        args = {"files": {"files": ("candidate.png", png(), "image/png")}, "headers": {"Idempotency-Key": "asset-upload"}}
        uploaded = c.post(url, **args)
        assert uploaded.status_code == 201
        assert c.post(url, **args).json() == uploaded.json()
        m = uploaded.json()["materials"][0]
        assert m["asset_id"] == a["id"] and m["variant_id"] is None
        state = c.get("/api/state").json()
        assert state["variants"] == [] and len(state["materials"]) == 1
        assert state["materials"][0]["width"] == 40
        assert state["materials"][0]["height"] == 30
        assert c.get(url).json()["total"] == 1
        v = c.post("/api/variants", json={"asset_id": a["id"], "name": "新状态"}).json()
        assert c.patch(f'/api/variants/{v["id"]}', json={"version": 1, "final_material_id": m["id"]}).status_code == 200


def test_image_orientation_and_dimensions_match_for_asset_and_candidate_uploads(tmp_path):
    def oriented_jpeg(color):
        stream = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 20), color).save(stream, "JPEG", exif=exif)
        return stream.getvalue()

    with TestClient(create_app(tmp_path)) as client:
        project = client.get("/api/state").json()["projects"][0]
        asset = client.post("/api/assets", json={"project_id": project["id"], "name": "方向测试"}).json()
        direct = client.post(
            f'/api/assets/{asset["id"]}/materials',
            files={"files": ("direct.jpg", oriented_jpeg("red"), "image/jpeg")},
        )
        assert direct.status_code == 201, direct.text
        direct_material = direct.json()["materials"][0]
        with client.app.state.service.connect() as connection:
            direct_file = connection.execute(
                "SELECT width,height FROM files WHERE id=?", (direct_material["file_id"],)
            ).fetchone()
        assert (direct_file["width"], direct_file["height"]) == (20, 40)

        candidate = client.post(
            "/api/candidates",
            data={"project_id": project["id"]},
            files={"files": ("candidate.jpg", oriented_jpeg("blue"), "image/jpeg")},
        )
        assert candidate.status_code == 201, candidate.text
        candidate_record = candidate.json()["candidates"][0]
        assert (candidate_record["width"], candidate_record["height"]) == (20, 40)

        for file_id in (direct_material["file_id"], candidate_record["file_id"]):
            with Image.open(tmp_path / "thumbnails" / f"{file_id}.jpg") as thumbnail:
                assert thumbnail.size == (20, 40)


def test_pool_cascade_includes_candidates_with_deleted_or_no_variant(tmp_path):
    s, _, a, v, mats = setup_tree(tmp_path)
    s.delete_entity("variant", v["id"], 1)
    direct = s.add_materials(None, [("direct.png", png())], asset_id=a["id"])["materials"][0]
    s.delete_material(mats[0]["id"])
    result = s.delete_entity("asset", a["id"], 1)
    assert result["affected"]["materials"] == 2
    assert not s.state()["materials"]
    with pytest.raises(HTTPException):
        s.restore_material(direct["id"])
    s.restore_entity("asset", a["id"])
    assert not s.state()["variants"]
    assert {m["id"] for m in s.state()["materials"]} == {mats[1]["id"], direct["id"]}


def test_legacy_material_migration_preserves_records_and_is_idempotent(tmp_path):
    s, _, a, v, mats = setup_tree(tmp_path)
    s.update_variant(v["id"], 1, final_material_id=mats[0]["id"])
    s.delete_material(mats[1]["id"])
    before = s.state()
    trash = s.trash()
    # Simulate the exact old table: required variant ownership and no asset_id.
    with sqlite3.connect(s.db) as c:
        c.execute("ALTER TABLE materials RENAME TO old_pool")
        c.execute("""CREATE TABLE materials(id TEXT PRIMARY KEY,variant_id TEXT NOT NULL REFERENCES variants(id),
            name TEXT NOT NULL,source TEXT NOT NULL,file_id TEXT NOT NULL REFERENCES files(id),created_at TEXT NOT NULL,
            deleted_at TEXT,delete_batch TEXT,version INTEGER NOT NULL DEFAULT 1)""")
        c.execute("""INSERT INTO materials(rowid,id,variant_id,name,source,file_id,created_at,deleted_at,delete_batch,version)
            SELECT rowid,id,variant_id,name,source,file_id,created_at,deleted_at,delete_batch,version FROM old_pool""")
        c.execute("DROP TABLE old_pool")
    migrated = AssetService(tmp_path)
    assert migrated.state() == before
    assert migrated.trash() == trash
    assert AssetService(tmp_path).state() == before
    with migrated.connect() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {r["asset_id"] for r in c.execute("SELECT * FROM materials")} == {a["id"]}


def test_restore_historical_variant_deletion_batch(tmp_path):
    s, _, a, v, mats = setup_tree(tmp_path)
    with s.connect(write=True) as c:
        c.execute("UPDATE variants SET deleted_at='old',delete_batch='legacy-batch' WHERE id=?", (v["id"],))
        c.execute("UPDATE materials SET deleted_at='old',delete_batch='legacy-batch' WHERE variant_id=?", (v["id"],))
    restored = s.restore_entity("variant", v["id"])
    assert restored["affected"]["materials"] == 2
    assert s.list_materials(asset_id=a["id"])["total"] == 2
