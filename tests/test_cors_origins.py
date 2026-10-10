import io

from fastapi.testclient import TestClient
from PIL import Image

from server import create_app


TRUSTED = "http://localhost:5173"


def test_exact_origin_allowlist_handles_read_write_and_preflight(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_ASSETS_CORS_ORIGINS", f"{TRUSTED}, https://staff.example.test")
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/state", headers={"Origin": TRUSTED})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == TRUSTED
        assert "access-control-allow-credentials" not in response.headers

        preflight = client.options(
            "/api/projects",
            headers={
                "Origin": TRUSTED,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,idempotency-key,x-upload-token",
            },
        )
        assert preflight.status_code == 200, preflight.text
        assert preflight.headers["access-control-allow-origin"] == TRUSTED
        assert "POST" in preflight.headers["access-control-allow-methods"]
        assert "idempotency-key" in preflight.headers["access-control-allow-headers"].lower()
        assert "x-upload-token" in preflight.headers["access-control-allow-headers"].lower()

        created = client.post(
            "/api/projects",
            json={"name": "独立前端项目"},
            headers={"Origin": TRUSTED, "Content-Type": "application/json"},
        )
        assert created.status_code == 201
        assert created.headers["access-control-allow-origin"] == TRUSTED


def test_unlisted_origin_cannot_read_or_write_and_wildcard_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_ASSETS_CORS_ORIGINS", TRUSTED)
    with TestClient(create_app(tmp_path)) as client:
        read = client.get("/api/state", headers={"Origin": "https://untrusted.example"})
        assert read.status_code == 403
        assert "access-control-allow-origin" not in read.headers

        write = client.post(
            "/api/projects",
            json={"name": "不可信来源"},
            headers={"Origin": "https://untrusted.example"},
        )
        assert write.status_code == 403
        assert write.json()["detail"]["code"] == "ORIGIN_NOT_ALLOWED"

        preflight = client.options(
            "/api/projects",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 400
        assert "access-control-allow-origin" not in preflight.headers

    monkeypatch.setenv("TEAM_ASSETS_CORS_ORIGINS", "*")
    try:
        create_app(tmp_path / "wildcard")
    except RuntimeError as exc:
        assert "exact" in str(exc)
    else:
        raise AssertionError("Wildcard CORS origins must not be accepted")


def test_same_origin_works_without_external_origins(tmp_path, monkeypatch):
    monkeypatch.delenv("TEAM_ASSETS_CORS_ORIGINS", raising=False)
    with TestClient(create_app(tmp_path), base_url="http://assets.internal") as client:
        same_origin = client.post(
            "/api/projects",
            json={"name": "同源"},
            headers={"Origin": "http://assets.internal"},
        )
        assert same_origin.status_code == 201
        project = same_origin.json()
        asset = client.post(
            "/api/assets",
            json={"project_id": project["id"], "name": "角色"},
        ).json()

        assert client.get(f"/api/assets/{asset['id']}/materials").status_code == 200


def test_multipart_upload_is_enabled_for_a_trusted_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_ASSETS_CORS_ORIGINS", TRUSTED)
    with TestClient(create_app(tmp_path)) as client:
        project = client.get("/api/state").json()["projects"][0]
        asset = client.post(
            "/api/assets",
            json={"project_id": project["id"], "name": "跨源上传"},
            headers={"Origin": TRUSTED},
        ).json()
        payload = io.BytesIO()
        Image.new("RGB", (16, 12), "green").save(payload, "PNG")
        response = client.post(
            f"/api/assets/{asset['id']}/materials",
            files={"files": ("green.png", payload.getvalue(), "image/png")},
            headers={"Origin": TRUSTED, "Idempotency-Key": "trusted-upload"},
        )
        assert response.status_code == 201, response.text
        assert response.headers["access-control-allow-origin"] == TRUSTED
