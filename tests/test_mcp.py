import base64
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from mcp_server import UploadImage, create_app, decode_uploads
from server import AssetService


HEADERS = {"Authorization": "Bearer test-secret", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def rpc(client, method, params=None):
    response = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert "error" not in result, result
    return result["result"]


def call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments})


def test_no_auth_default_and_bearer_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("TEAM_ASSETS_MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_MODE", raising=False)
    with TestClient(create_app(tmp_path / "public")) as client:
        assert call(client, "get_capabilities", {})["structuredContent"]["authentication"]["mode"] == "none"
        response = client.post("/mcp", headers={k: v for k, v in HEADERS.items() if k != "Authorization"},
                               json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert response.status_code == 200, response.text
    with TestClient(create_app(tmp_path / "auth", "test-secret")) as client:
        for method in ("get", "post", "delete"):
            assert getattr(client, method)("/mcp").status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
        tools = rpc(client, "tools/list")["tools"]
        assert {item["name"] for item in tools} >= {"get_workspace", "upload_materials", "set_final_material"}


def test_skill_manifest_and_download(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        manifest = call(client, "get_skill_manifest", {})["structuredContent"]
        assert manifest["version"] == (Path(__file__).resolve().parents[1] / "mcp-client" / "VERSION").read_text(encoding="utf-8").strip()
        assert manifest["download_path"] == "/mcp-client/download"
        response = client.get(manifest["download_path"])
        assert response.status_code == 200
        assert response.headers["X-Team-Assets-Client-SHA256"] == manifest["sha256"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "team-assets-mcp-client/SKILL.md" in archive.namelist()
            assert "team-assets-mcp-client/VERSION" in archive.namelist()


def test_real_protocol_upload_retry_conflict_and_restore(tmp_path):
    # A second service instance emulates the web process sharing SQLite and files.
    web = AssetService(tmp_path)
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        project = call(client, "create_project", {"name": "MCP项目"})["structuredContent"]
        asset = call(client, "create_asset", {"project_id": project["id"], "name": "角色"})["structuredContent"]
        variant = call(client, "create_variant", {"asset_id": asset["id"], "name": "战衣"})["structuredContent"]
        output = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(output, "PNG")
        args = {"variant_id": variant["id"], "idempotency_key": "upload-1", "files": [{"filename": "render.png", "base64_data": base64.b64encode(output.getvalue()).decode()}]}
        uploaded = call(client, "upload_materials", args)["structuredContent"]
        assert call(client, "upload_materials", args)["structuredContent"] == uploaded
        material = uploaded["materials"][0]
        assert len(web.state()["materials"]) == 1

        edited = call(client, "update_asset", {"asset_id": asset["id"], "version": 1, "name": "新角色", "category": "道具", "role": "剧情线索"})
        assert not edited.get("isError")
        assert edited["structuredContent"]["version"] == 2
        assert web.state()["assets"][0]["category"] == "道具"
        assert web.state()["assets"][0]["role"] == "剧情线索"
        assert web.state()["variants"][0]["asset_id"] == asset["id"]
        assert len(web.state()["materials"]) == 1
        conflict = call(client, "update_asset", {"asset_id": asset["id"], "version": 1, "name": "旧修改"})
        assert conflict["isError"] is True
        assert "409" in conflict["content"][0]["text"]
        invalid = call(client, "update_asset", {"asset_id": asset["id"], "version": 2, "category": "  "})
        assert invalid["isError"] is True
        assert "422" in invalid["content"][0]["text"]
        assert material["source"] == "ai"
        assert web.state()["variants"][0]["final_material_id"] is None
        final = call(client, "set_final_material", {"variant_id": variant["id"], "material_id": material["id"], "version": 1})
        assert not final.get("isError")
        conflict = call(client, "update_variant", {"variant_id": variant["id"], "version": 1, "name": "old"})
        assert conflict["isError"] is True
        assert "409" in conflict["content"][0]["text"]
        assert not call(client, "delete_material", {"material_id": material["id"]}).get("isError")
        assert not web.state()["materials"]
        assert not call(client, "restore_material", {"material_id": material["id"]}).get("isError")
        assert len(web.state()["materials"]) == 1


def test_upload_validation_and_streamed_body_limit(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Base64"):
        decode_uploads([UploadImage(filename="x.png", base64_data="https://example.com/x.png")])
    with pytest.raises(ValueError):
        decode_uploads([])
    monkeypatch.setattr("mcp_server.MAX_REQUEST_BYTES", 10)
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        response = client.post("/mcp", headers=HEADERS, content=iter([b"123456", b"789012"]))
        assert response.status_code == 413


def test_incomplete_upload_is_not_marked_retryable(tmp_path):
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        session = call(client, "begin_upload", {"filename": "partial.png", "size": 12})["structuredContent"]
        result = call(client, "complete_upload", {"upload_id": session["id"]})
        assert result["isError"] is True
        error = result["structuredContent"]["error"]
        assert error["code"] == "upload_incomplete"
        assert error["status"] == 409
        assert error["retryable"] is False
        assert error["offset"] == 0 and error["size"] == 12


def test_register_candidate_exposes_unambiguous_folder_path(tmp_path):
    service = AssetService(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, "PNG")
    image.seek(0)
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        project = call(client, "create_project", {"name": "目录边界测试"})["structuredContent"]
        uploaded = service.add_candidate_uploads(
            project["id"], [("seed.png", image, "image/png")]
        )["candidates"][0]

        listed = rpc(client, "tools/list")["tools"]
        register_schema = next(tool for tool in listed if tool["name"] == "register_candidate")["inputSchema"]
        assert "folder_path" in register_schema["properties"]

        result = call(client, "register_candidate", {
            "project_id": project["id"],
            "file_id": uploaded["file_id"],
            "filename": "shot01",
            "folder_path": "episode1/shot01",
        })["structuredContent"]
        assert result["folder_path"] == "episode1/shot01"
        assert result["relative_path"] == "episode1/shot01/shot01"
