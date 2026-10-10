import io

from fastapi.testclient import TestClient
from PIL import Image

from mcp_server import create_app as create_mcp_app
from server import create_app


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (20, 12), "orange").save(stream, "PNG")
    return stream.getvalue()


def test_v1_contract_covers_crud_files_history_and_keeps_legacy_routes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json").json()
        assert spec["openapi"].startswith("3.")
        assert "/api/v1/projects" in spec["paths"]
        assert "/api/v1/assets/{asset_id}" in spec["paths"]
        assert spec["paths"]["/api/v1/assets/{asset_id}"]["patch"]["responses"]["409"]
        assert spec["paths"]["/api/projects"]["post"]["deprecated"] is True
        for path, operations in spec["paths"].items():
            if not path.startswith("/api/v1/") or path.endswith("/events") or "/files/" in path:
                continue
            for operation in operations.values():
                success = operation["responses"].get("200") or operation["responses"].get("201")
                assert success and success.get("content", {}).get("application/json", {}).get("schema"), path

        project = client.post("/api/v1/projects", json={"name": "平台项目"})
        assert project.status_code == 201, project.text
        project = project.json()
        assert project["version"] == 1
        assert client.get(f"/api/v1/projects/{project['id']}").json()["id"] == project["id"]

        projects = client.get("/api/v1/projects?sort_by=name&order=asc&limit=10")
        assert projects.status_code == 200
        assert projects.json()["total"] >= 2

        asset = client.post(
            "/api/v1/assets",
            json={"project_id": project["id"], "name": "女主", "category": "人物", "role": "主角"},
        )
        assert asset.status_code == 201, asset.text
        asset = asset.json()
        variant = client.post(
            "/api/v1/variants",
            json={"asset_id": asset["id"], "name": "初始造型", "episodes": "1-3"},
        )
        assert variant.status_code == 201, variant.text
        variant = variant.json()
        assert variant["episode_scope"]["kind"] == "ranges"
        assert client.get(f"/api/v1/variants?asset_id={asset['id']}").json()["variants"][0]["id"] == variant["id"]

        uploaded = client.post(
            f"/api/v1/assets/{asset['id']}/materials",
            files={"files": ("final.png", png_bytes(), "image/png")},
            headers={"Idempotency-Key": "v1-image-1"},
        )
        assert uploaded.status_code == 201, uploaded.text
        material = uploaded.json()["materials"][0]
        assert material["url"].startswith("/api/v1/files/")
        assert material["thumbnail_url"].startswith("/api/v1/files/")
        finalized = client.patch(
            f"/api/v1/variants/{variant['id']}/final-material",
            json={"version": variant["version"], "material_id": material["id"]},
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["final_material_id"] == material["id"]
        assert client.get(f"/api/v1/materials/{material['id']}").json()["id"] == material["id"]
        assert client.get(material["url"]).content == png_bytes()
        assert client.get(f"/api/v1/files/{material['file_id']}?thumbnail=true").headers["content-type"] == "image/jpeg"

        changed_asset = client.patch(
            f"/api/v1/assets/{asset['id']}",
            json={"version": asset["version"], "name": "正式名称"},
        )
        assert changed_asset.status_code == 200
        asset = changed_asset.json()
        stale = client.patch(
            f"/api/v1/assets/{asset['id']}",
            json={"version": 1, "name": "旧修改"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

        preview = client.post(
            f"/api/v1/assets/{asset['id']}/delete-preview", json={"version": asset["version"]}
        )
        assert preview.status_code == 200
        assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 200

        deleted = client.request(
            "DELETE", f"/api/v1/assets/{asset['id']}", json={"version": asset["version"]}
        )
        assert deleted.status_code == 200
        assert client.get(f"/api/v1/trash?entity_type=asset").json()["items"][0]["id"] == asset["id"]
        restored = client.post(f"/api/v1/assets/{asset['id']}/restore")
        assert restored.status_code == 200
        assert client.get("/api/v1/history?limit=10").json()["total"] > 0

        # Existing official-client paths stay operational during migration.
        assert client.get("/api/state").status_code == 200
        assert client.post("/api/projects", json={"name": "旧入口", "legacy_extra": True}).status_code == 201
        strict_v1 = client.post("/api/v1/projects", json={"name": "strict", "legacy_extra": True})
        assert strict_v1.status_code == 422


def test_v1_candidate_library_uses_shared_service_and_revision_conflicts(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project = client.get("/api/v1/projects").json()["projects"][0]
        upload = client.post(
            "/api/v1/candidates",
            data={"project_id": project["id"], "category": "人物"},
            files=[
                ("files", ("folder/one.png", png_bytes(), "image/png")),
                ("files", ("folder/two.png", png_bytes(), "image/png")),
                ("files", ("root.png", png_bytes(), "image/png")),
            ],
            headers={"Idempotency-Key": "v1-candidates-1"},
        )
        assert upload.status_code == 201, upload.text
        candidates = upload.json()["candidates"]
        assert len(candidates) == 3
        assert candidates[0]["url"].startswith("/api/v1/files/")
        folders = client.get(f"/api/v1/candidate-folders?project_id={project['id']}").json()["folders"]
        assert [folder["path"] for folder in folders] == ["folder"]
        listing = client.get(f"/api/v1/candidates?project_id={project['id']}&sort_by=name")
        assert listing.status_code == 200
        assert listing.json()["total"] == 3

        revision = client.get("/api/v1/revision").json()["revision"]
        root_candidate = next(item for item in candidates if item["folder_path"] == "")
        entries = [
            {"type": "folder", "id": folders[0]["id"]},
            {"type": "candidate", "id": root_candidate["id"]},
        ]
        ordered = client.post(
            "/api/v1/candidate-entries/reorder",
            json={"project_id": project["id"], "parent_path": "", "expected_revision": revision, "entries": entries},
        )
        assert ordered.status_code == 200, ordered.text

        stale_order = client.post(
            "/api/v1/candidate-entries/reorder",
            json={"project_id": project["id"], "parent_path": "", "expected_revision": revision, "entries": entries},
        )
        assert stale_order.status_code == 409
        assert stale_order.json()["detail"]["code"] == "REVISION_CONFLICT"


def test_v1_resumable_upload_intake_and_project_import_are_idempotent(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project = client.get("/api/v1/projects").json()["projects"][0]
        content = png_bytes()
        session = client.post(
            "/api/v1/uploads",
            json={"filename": "intake.png", "size": len(content), "idempotency_key": "v1-upload-1"},
        )
        assert session.status_code == 201, session.text
        session = session.json()
        assert session["upload_url"].startswith("/api/v1/uploads/")
        accepted = client.put(
            session["upload_url"] + "?offset=0",
            content=content,
            headers={"X-Upload-Token": session["upload_token"], "Content-Type": "application/octet-stream"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["offset"] == len(content)
        unauthenticated_status = client.get(session["upload_url"])
        assert unauthenticated_status.status_code == 401
        assert client.get(
            session["upload_url"], headers={"X-Upload-Token": session["upload_token"]}
        ).json()["offset"] == len(content)
        unauthenticated_complete = client.post(f"{session['upload_url']}/complete")
        assert unauthenticated_complete.status_code == 401
        ready = client.post(
            f"{session['upload_url']}/complete",
            headers={"X-Upload-Token": session["upload_token"]},
        )
        assert ready.status_code == 200, ready.text
        file_id = ready.json()["file_id"]

        intake_body = {
            "project_id": project["id"],
            "asset": {"name": "AI 入库资产", "category": "人物", "role": "主角"},
            "variants": [{"name": "初始造型", "episodes": "all", "file_ids": [file_id]}],
            "idempotency_key": "v1-intake-1",
        }
        intake = client.post("/api/v1/intake/commit", json=intake_body)
        assert intake.status_code == 201, intake.text
        assert len(intake.json()["materials"]) == 1
        assert client.post("/api/v1/intake/commit", json=intake_body).json() == intake.json()

        manifest = {
            "project_id": project["id"],
            "assets": [{"name": "批量导入", "variants": [{"name": "状态一", "file_ids": [file_id]}]}],
        }
        planned = client.post("/api/v1/project-imports/plan", json=manifest)
        assert planned.status_code == 200, planned.text
        assert planned.json()["valid"] is True
        apply_body = {**manifest, "idempotency_key": "v1-project-import-1"}
        applied = client.post("/api/v1/project-imports/apply", json=apply_body)
        assert applied.status_code == 201, applied.text
        assert len(applied.json()["assets"]) == 1
        assert client.post("/api/v1/project-imports/apply", json=apply_body).json() == applied.json()


def test_rest_and_mcp_share_service_rules_and_the_same_database(tmp_path):
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(create_app(tmp_path / "data")) as rest, TestClient(
        create_mcp_app(data_dir=tmp_path / "data", auth_mode="none")
    ) as mcp:
        project = rest.post("/api/v1/projects", json={"name": "跨接口共享"}).json()
        asset = rest.post(
            "/api/v1/assets", json={"project_id": project["id"], "name": "共同资产"}
        ).json()

        workspace = mcp.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_workspace", "arguments": {}},
            },
        ).json()["result"]["structuredContent"]
        assert project["id"] in {item["id"] for item in workspace["projects"]}
        assert asset["id"] in {item["id"] for item in workspace["assets"]}

        rest_edit = rest.patch(
            f"/api/v1/assets/{asset['id']}", json={"version": 1, "name": "REST 已更新"}
        )
        assert rest_edit.status_code == 200
        mcp_conflict = mcp.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "update_asset",
                    "arguments": {"asset_id": asset["id"], "version": 1, "name": "MCP stale"},
                },
            },
        ).json()["result"]
        assert mcp_conflict["isError"] is True
        assert mcp_conflict["structuredContent"]["error"]["code"] == "VERSION_CONFLICT"
        assert rest.get(f"/api/v1/assets/{asset['id']}").json()["name"] == "REST 已更新"
