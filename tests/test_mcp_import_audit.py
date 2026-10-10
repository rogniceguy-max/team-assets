import json

from starlette.testclient import TestClient

from mcp_server import create_app


HEADERS = {
    "Authorization": "Bearer test-secret",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


def call(client, name, arguments=None):
    response = client.post(
        "/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments or {}}},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def ok(client, name, arguments=None):
    result = call(client, name, arguments)
    assert not result.get("isError"), result
    return result["structuredContent"]


def test_episode_usages_and_coverage_audit(tmp_path):
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        project = ok(client, "create_project", {"name": "审计项目"})
        asset = ok(client, "create_asset", {"project_id": project["id"], "name": "角色"})
        variant = ok(client, "create_variant", {
            "asset_id": asset["id"], "name": "门外声音", "episodes": "3-10",
            "episode_usages": [{"episodes": "3", "mode": "voice", "note": "门外"}],
        })
        assert variant["episode_usages"][0]["mode"] == "voice"
        report = ok(client, "audit_project_asset_coverage", {"project_id": project["id"]})
        assert report["summary"]["variants_missing_materials"] == 1
        assert report["assets"][0]["variants"][0]["coverage_status"] == "missing"
        assert report["assets"][0]["coverage_status"] == "missing"
        assert report["assets"][0]["candidate_coverage_status"] == "missing"

        import io
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(image, "PNG")
        service = client.app.state.service
        uploaded = service.add_materials(
            variant["id"], [("direct.png", image.getvalue())], idempotency_key="coverage-direct-file"
        )
        current_variant = service.get_variant(variant["id"])
        service.update_variant(
            variant["id"], current_variant["version"],
            final_material_id=uploaded["materials"][0]["id"],
        )
        report = ok(client, "audit_project_asset_coverage", {"project_id": project["id"]})
        direct_asset = report["assets"][0]
        assert direct_asset["material_count"] == 1
        assert direct_asset["coverage_status"] == "has_materials"
        assert direct_asset["candidate_coverage_status"] == "missing"
        assert direct_asset["variants"][0]["coverage_status"] == "finalized"
        assert report["summary"]["coverage_percent"] == 100


def test_project_import_plan_apply_is_idempotent(tmp_path):
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        project = ok(client, "create_project", {"name": "批量项目"})
        manifest = {"project_id": project["id"], "assets": [{
            "name": "洛森", "category": "人物", "variants": [{
                "name": "默认", "episodes": "19-39",
                "episode_usages": [{"episodes": "19-39", "mode": "on_screen"}],
            }],
        }]}
        plan = ok(client, "plan_project_import", manifest)
        assert plan["valid"] is True
        applied = ok(client, "apply_project_import", {**manifest, "idempotency_key": "import-1"})
        assert applied["assets"][0]["name"] == "洛森"
        assert ok(client, "apply_project_import", {**manifest, "idempotency_key": "import-1"}) == applied
        conflict = call(client, "apply_project_import", {**manifest, "idempotency_key": "import-1", "on_name_conflict": "reuse"})
        assert conflict.get("isError") is True


def test_candidate_reference_keeps_match_metadata(tmp_path):
    with TestClient(create_app(tmp_path, "test-secret")) as client:
        project = ok(client, "create_project", {"name": "候选项目"})
        asset = ok(client, "create_asset", {"project_id": project["id"], "name": "角色"})
        variant = ok(client, "create_variant", {"asset_id": asset["id"], "name": "默认"})
        import base64, io
        from PIL import Image
        body = io.BytesIO(); Image.new("RGB", (2, 2), "red").save(body, "PNG")
        uploaded = ok(client, "upload_materials", {"asset_id": asset["id"], "files": [{"filename": "candidate.png", "base64_data": base64.b64encode(body.getvalue()).decode()}], "idempotency_key": "candidate-file"})
        candidate = ok(client, "register_candidate", {"project_id": project["id"], "file_id": uploaded["materials"][0]["file_id"], "filename": "candidate.png"})
        result = ok(client, "create_candidate_reference", {"candidate_id": candidate["id"], "asset_id": asset["id"], "variant_id": variant["id"], "match_status": "approximate", "confidence": 0.7, "note": "同文件夹推断", "source": "import"})
        assert result["reference"]["match_status"] == "approximate"
        assert result["reference"]["confidence"] == 0.7
