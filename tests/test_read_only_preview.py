import io
import sqlite3

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.testclient import TestClient

from mcp_server import create_app as create_mcp_app
from scripts.create_preview_snapshot import create_preview_snapshot
from server import AssetService, create_app
from uploads import UploadManager


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(stream, "PNG")
    return stream.getvalue()


def make_project(service):
    project = service.create_project("只读预览测试")
    asset = service.create_asset(project["id"], "角色")
    return project, asset


def test_read_only_service_and_rest_reject_all_writes(tmp_path):
    writable = AssetService(tmp_path / "data")
    project, asset = make_project(writable)
    material = writable.add_materials(
        None,
        [("preview.png", image_bytes())],
        asset_id=asset["id"],
    )["materials"][0]
    preview_service = AssetService(tmp_path / "data", read_only=True)
    original_state = preview_service.state()

    with TestClient(create_app(service=preview_service)) as client:
        assert client.get("/api/state").status_code == 200
        assert client.get(material["url"]).content == image_bytes()
        preview_delete = client.post(
            f"/api/v1/assets/{asset['id']}/delete-preview", json={"version": asset["version"]}
        )
        assert preview_delete.status_code == 200, preview_delete.text
        for method, path, kwargs in (
            ("post", "/api/projects", {"json": {"name": "不能写入"}}),
            ("patch", f"/api/assets/{asset['id']}", {"json": {"version": 1, "name": "不能改"}}),
            ("delete", f"/api/materials/{material['id']}", {}),
            ("post", f"/api/materials/{material['id']}/restore", {}),
        ):
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "READ_ONLY"

    assert preview_service.state() == original_state
    assert not list((tmp_path / "data" / "staging").glob("*.candidate"))
    with pytest.raises(HTTPException) as error:
        preview_service.add_materials(None, [], asset_id=asset["id"])
    assert error.value.status_code == 403


def test_read_only_mcp_keeps_queries_but_blocks_mutations_and_uploads(tmp_path):
    writable = AssetService(tmp_path / "data")
    project, _asset = make_project(writable)
    writable_uploads = UploadManager(writable)
    session = writable_uploads.begin_upload("partial.png", 8)
    staged_path = writable_uploads.path(session["id"])
    staged_path.write_bytes(b"partial")
    with writable.connect(write=True) as connection:
        connection.execute("UPDATE upload_sessions SET expires_at=0 WHERE id=?", (session["id"],))

    preview_service = AssetService(tmp_path / "data", read_only=True)
    app = create_mcp_app(service=preview_service, auth_mode="none")
    assert app.state.service is preview_service
    assert app.state.uploads.routes == []
    assert staged_path.is_file()
    with writable.connect() as connection:
        assert connection.execute(
            "SELECT status FROM upload_sessions WHERE id=?", (session["id"],)
        ).fetchone()[0] == "uploading"

    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(app) as client:
        listed = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_workspace", "arguments": {}},
        })
        assert listed.status_code == 200
        assert project["id"] in {
            item["id"] for item in listed.json()["result"]["structuredContent"]["projects"]
        }

        changed = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "create_project", "arguments": {"name": "不应创建"}},
        })
        assert changed.status_code == 200
        assert changed.json()["result"]["isError"] is True
        assert changed.json()["result"]["structuredContent"]["error"]["code"] == "READ_ONLY"

        upload = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "begin_upload", "arguments": {"filename": "x.png", "size": 1}},
        })
        assert upload.json()["result"]["isError"] is True
        assert upload.json()["result"]["structuredContent"]["error"]["code"] == "READ_ONLY"

    assert project["id"] in {p["id"] for p in preview_service.state()["projects"]}


def test_read_only_database_never_runs_schema_migrations(tmp_path):
    root = tmp_path / "minimal"
    database = root / "database" / "assets.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE preserved(value TEXT)")

    service = AssetService(root, read_only=True)
    assert service.read_only is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("preserved",)]
    assert not (root / "originals").exists()
    assert not (root / "staging").exists()


def test_preview_snapshot_is_independent_and_uses_sqlite_backup(tmp_path):
    source = tmp_path / "source"
    service = AssetService(source)
    project, asset = make_project(service)
    service.add_materials(None, [("preview.png", image_bytes())], asset_id=asset["id"])
    destination = tmp_path / "preview-snapshot"

    result = create_preview_snapshot(source, destination)
    assert result == destination
    snapshot_service = AssetService(destination, read_only=True)
    assert project["id"] in {p["id"] for p in snapshot_service.state()["projects"]}
    with pytest.raises(HTTPException) as error:
        snapshot_service.create_project("拒绝写入")
    assert error.value.status_code == 403
    with pytest.raises(FileExistsError):
        create_preview_snapshot(source, destination)


def test_preview_snapshot_rejects_a_destination_inside_source(tmp_path):
    source = tmp_path / "source"
    AssetService(source)
    with pytest.raises(ValueError, match="inside"):
        create_preview_snapshot(source, source / "preview")
