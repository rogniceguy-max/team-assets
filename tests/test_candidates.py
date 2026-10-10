import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from PIL import Image

from server import AssetService, create_app


def png(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(stream, "PNG")
    return stream.getvalue()


def project_and_asset(client):
    project = client.get("/api/state").json()["projects"][0]
    asset = client.post("/api/assets", json={"project_id": project["id"], "name": "候选目标"}).json()
    variant = client.post("/api/variants", json={"asset_id": asset["id"], "name": "状态"}).json()
    return project, asset, variant


def test_candidate_upload_keeps_duplicates_and_folder_path(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        body = png()
        response = client.post(
            "/api/candidates",
            data={"project_id": project["id"]},
            files=[
                ("files", ("第一集/beat 1/a.png", body, "image/png")),
                ("files", ("第二集/a.png", body, "image/png")),
            ],
        )
        assert response.status_code == 201, response.text
        candidates = response.json()["candidates"]
        assert len(candidates) == 2
        assert candidates[0]["name"] == "a.png"
        assert candidates[0]["relative_path"] == "第一集/beat 1/a.png"
        assert candidates[0]["file_id"] == candidates[1]["file_id"]
        assert client.get("/api/candidates", params={"project_id": project["id"]}).json()["total"] == 2
        assert client.get("/api/candidates", params={"project_id": project["id"], "query": "a.png"}).json()["total"] == 2


def test_candidate_upload_idempotency_replays_and_rejects_changed_payload(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        headers = {"Idempotency-Key": "candidate-upload-retry-1"}
        request = {
            "data": {"project_id": project["id"], "category": "人物"},
            "files": {"files": ("same.png", png("red"), "image/png")},
            "headers": headers,
        }

        first = client.post("/api/candidates", **request)
        replay = client.post("/api/candidates", **request)
        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()

        changed = client.post(
            "/api/candidates",
            data={"project_id": project["id"], "category": "人物"},
            files={"files": ("same.png", png("blue"), "image/png")},
            headers=headers,
        )
        assert changed.status_code == 409
        assert client.get("/api/candidates", params={"project_id": project["id"]}).json()["total"] == 1


def test_candidate_upload_compensates_original_when_thumbnail_move_fails(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        service = client.app.state.service
        original_replace = Path.replace

        def fail_thumbnail_replace(path, target):
            if Path(target).parent.name == "thumbnails":
                raise OSError("injected thumbnail move failure")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_thumbnail_replace)
        with pytest.raises(HTTPException) as error:
            service.add_candidate_uploads(
                project["id"], [("failure.png", io.BytesIO(png()), "image/png")]
            )
        assert error.value.status_code == 507
        assert not list((tmp_path / "originals").iterdir())
        assert not list((tmp_path / "thumbnails").iterdir())
        with service.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_candidate_batch_calculates_next_sort_order_once_per_folder(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        service = client.app.state.service
        calculate = service._next_candidate_order
        calls = []

        def count_calculation(connection, project_id, folder_path):
            calls.append(folder_path)
            return calculate(connection, project_id, folder_path)

        service._next_candidate_order = count_calculation
        response = client.post(
            "/api/candidates",
            data={"project_id": project["id"]},
            files=[
                ("files", ("A/a1.png", png("red"), "image/png")),
                ("files", ("A/a2.png", png("blue"), "image/png")),
                ("files", ("A/sub/a3.png", png("green"), "image/png")),
                ("files", ("A/sub/a4.png", png("yellow"), "image/png")),
                ("files", ("B/b1.png", png("purple"), "image/png")),
            ],
        )

        assert response.status_code == 201, response.text
        assert sorted(calls) == ["", "A", "A/sub", "B"]
        by_folder = {}
        for candidate in response.json()["candidates"]:
            by_folder.setdefault(candidate["folder_path"], []).append(candidate["sort_order"])
        assert by_folder == {"A": [0.0, 1.0], "A/sub": [0.0, 1.0], "B": [0.0]}


def test_register_candidate_explicit_folder_path_keeps_matching_basename(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        uploaded = client.post(
            "/api/candidates",
            data={"project_id": project["id"]},
            files={"files": ("seed.png", png(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = uploaded.json()["candidates"][0]["file_id"]

        response = client.post(
            "/api/candidates/from-file",
            json={
                "project_id": project["id"],
                "file_id": file_id,
                "filename": "shot01",
                "folder_path": "episode1/shot01",
            },
        )
        assert response.status_code == 201, response.text
        candidate = response.json()
        assert candidate["folder_path"] == "episode1/shot01"
        assert candidate["relative_path"] == "episode1/shot01/shot01"

        conflict = client.post(
            "/api/candidates/from-file",
            json={
                "project_id": project["id"],
                "file_id": file_id,
                "filename": "shot01",
                "folder_path": "episode1/shot01",
                "relative_path": "episode1/shot01/shot01",
            },
        )
        assert conflict.status_code == 422


def test_candidate_status_highlight_and_reference_reuses_file(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, asset, variant = project_and_asset(client)
        uploaded = client.post(
            "/api/candidates", data={"project_id": project["id"]},
            files={"files": ("候选.png", png(), "image/png")},
        ).json()["candidates"][0]
        assert uploaded["status"] == "active"
        disabled = client.patch(
            f"/api/candidates/{uploaded['id']}",
            json={"version": uploaded["version"], "status": "disabled"},
        )
        assert disabled.status_code == 200
        assert client.get("/api/candidates", params={"project_id": project["id"]}).json()["total"] == 0
        assert client.get("/api/candidates", params={"project_id": project["id"], "status": "disabled"}).json()["total"] == 1
        highlighted = client.post(f"/api/candidates/{uploaded['id']}/highlight")
        assert highlighted.status_code == 200
        assert highlighted.json()["highlighted"] is True

        reference = client.post(
            f"/api/candidates/{uploaded['id']}/references",
            json={"asset_id": asset["id"], "variant_id": variant["id"]},
        )
        assert reference.status_code == 201, reference.text
        result = reference.json()
        assert result["material"]["candidate_id"] == uploaded["id"]
        assert client.get(f"/api/assets/{asset['id']}/materials").json()["total"] == 1
        assert client.post(
            f"/api/candidates/{uploaded['id']}/references",
            json={"asset_id": asset["id"], "variant_id": variant["id"]},
        ).status_code == 201
        with client.app.state.service.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM materials WHERE candidate_id=?", (uploaded["id"],)).fetchone()[0] == 1

        # Parent soft deletion hides the relation but does not destroy it.
        service = client.app.state.service
        service.delete_entity("asset", asset["id"], 1)
        assert client.get(f"/api/candidates/{uploaded['id']}").json()["references"] == []
        service.restore_entity("asset", asset["id"])
        assert len(client.get(f"/api/candidates/{uploaded['id']}").json()["references"]) == 1


def test_candidate_requires_same_project_for_reference(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        other = client.post("/api/projects", json={"name": "其它项目"}).json()
        other_asset = client.post("/api/assets", json={"project_id": other["id"], "name": "其它资产"}).json()
        candidate = client.post(
            "/api/candidates", data={"project_id": project["id"]},
            files={"files": ("a.png", png(), "image/png")},
        ).json()["candidates"][0]
        response = client.post(
            f"/api/candidates/{candidate['id']}/references",
            json={"asset_id": other_asset["id"]},
        )
        assert response.status_code == 422


def test_candidate_folders_mixed_order_move_rename_and_inherited_disable(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        response = client.post(
            "/api/candidates", data={"project_id": project["id"]}, files=[
                ("files", ("A/a.png", png("red"), "image/png")),
                ("files", ("A/B/b.png", png("blue"), "image/png")),
                ("files", ("root.png", png("green"), "image/png")),
            ],
        )
        assert response.status_code == 201, response.text
        uploaded = response.json()["candidates"]
        folders = client.get("/api/candidate-folders", params={"project_id": project["id"]}).json()["folders"]
        folder_a = next(folder for folder in folders if folder["path"] == "A")
        folder_b = next(folder for folder in folders if folder["path"] == "A/B")
        assert folder_b["parent_path"] == "A"

        root = next(item for item in uploaded if item["folder_path"] == "")
        moved = client.post(
            f"/api/candidates/{root['id']}/move",
            json={"version": root["version"], "target_folder_path": "A"},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["folder_path"] == "A"

        renamed = client.patch(
            f"/api/candidate-folders/{folder_a['id']}",
            json={"version": folder_a["version"], "name": "Renamed"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["path"] == "Renamed"
        all_candidates = client.get("/api/candidates", params={"project_id": project["id"], "status": "all"}).json()["candidates"]
        assert {item["folder_path"] for item in all_candidates} == {"Renamed", "Renamed/B"}

        disable = client.patch(
            f"/api/candidate-folders/{renamed.json()['id']}",
            json={"version": renamed.json()["version"], "status": "disabled"},
        )
        assert disable.status_code == 200, disable.text
        assert client.get("/api/candidates", params={"project_id": project["id"]}).json()["total"] == 0
        assert client.get("/api/candidates", params={"project_id": project["id"], "status": "disabled"}).json()["total"] == 3
        restored = client.patch(
            f"/api/candidate-folders/{renamed.json()['id']}",
            json={"version": disable.json()["version"], "status": "active"},
        )
        assert restored.status_code == 200, restored.text
        assert client.get("/api/candidates", params={"project_id": project["id"]}).json()["total"] == 3


def test_candidate_pagination_filters_inherited_disabled_before_limit(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        project, _, _ = project_and_asset(client)
        uploaded = client.post(
            "/api/candidates",
            data={"project_id": project["id"]},
            files=[
                ("files", ("A/a1.png", png("red"), "image/png")),
                ("files", ("A/a2.png", png("blue"), "image/png")),
                ("files", ("B/b1.png", png("green"), "image/png")),
                ("files", ("B/b2.png", png("yellow"), "image/png")),
                ("files", ("root.png", png("purple"), "image/png")),
            ],
        )
        assert uploaded.status_code == 201, uploaded.text
        records = uploaded.json()["candidates"]
        folders = client.get("/api/candidate-folders", params={"project_id": project["id"]}).json()["folders"]
        folder_a = next(folder for folder in folders if folder["path"] == "A")
        assert client.patch(
            f"/api/candidate-folders/{folder_a['id']}",
            json={"version": folder_a["version"], "status": "disabled"},
        ).status_code == 200
        b2 = next(record for record in records if record["name"] == "b2.png")
        assert client.patch(
            f"/api/candidates/{b2['id']}",
            json={"version": b2["version"], "status": "disabled"},
        ).status_code == 200

        active_page = client.get(
            "/api/candidates", params={"project_id": project["id"], "limit": 1, "offset": 1}
        ).json()
        assert active_page["total"] == 2
        assert [item["name"] for item in active_page["candidates"]] == ["b1.png"]
        disabled_page = client.get(
            "/api/candidates",
            params={"project_id": project["id"], "status": "disabled", "limit": 1, "offset": 1},
        ).json()
        assert disabled_page["total"] == 3
        assert [item["name"] for item in disabled_page["candidates"]] == ["a2.png"]
        assert disabled_page["candidates"][0]["disabled_by_folder"] is True
        all_page = client.get(
            "/api/candidates",
            params={"project_id": project["id"], "status": "all", "limit": 2, "offset": 0},
        ).json()
        assert all_page["total"] == 5
        assert len(all_page["candidates"]) == 2
        assert "_inherited_disabled" not in all_page["candidates"][0]
