import io

from PIL import Image

from server import AssetService
from services.candidate_service import CandidateMixin
from uploads import UploadManager


def png():
    stream = io.BytesIO()
    Image.new("RGB", (12, 12), "orange").save(stream, "PNG")
    return stream.getvalue()


def test_candidate_folder_backfill_is_recorded_and_runs_once(tmp_path, monkeypatch):
    service = AssetService(tmp_path)
    project = service.list_projects()["projects"][0]
    uploaded = service.add_candidate_uploads(
        project["id"], [("Legacy/Sub/item.png", io.BytesIO(png()), "image/png")]
    )["candidates"][0]

    # Recreate an older database state: schema columns exist, but the one-time
    # folder-path backfill has not been recorded and folder rows are missing.
    with service.connect(write=True) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=1")
        connection.execute("DELETE FROM candidate_folders WHERE project_id=?", (project["id"],))
        connection.execute("UPDATE candidates SET folder_path='' WHERE id=?", (uploaded["id"],))

    migrated = AssetService(tmp_path)
    candidate = migrated.get_candidate(uploaded["id"])
    assert candidate["folder_path"] == "Legacy/Sub"
    folders = migrated.list_candidate_folders(project["id"])["folders"]
    assert {folder["path"] for folder in folders} == {"Legacy", "Legacy/Sub"}

    with migrated.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=1"
        ).fetchone()[0] == 1
        indexes = {
            table: {row["name"] for row in connection.execute(f"PRAGMA index_list({table})")}
            for table in ("variants", "materials", "candidates", "candidate_folders", "candidate_references")
        }
    UploadManager(migrated)
    with migrated.connect() as connection:
        upload_indexes = {row["name"] for row in connection.execute("PRAGMA index_list(upload_sessions)")}

    assert "variants_final_material" in indexes["variants"]
    assert "materials_file_id" in indexes["materials"]
    assert "candidates_file_id" in indexes["candidates"]
    assert "candidate_folders_disabled_project" in indexes["candidate_folders"]
    assert "candidate_refs_variant" in indexes["candidate_references"]
    assert "upload_sessions_status_expiry" in upload_indexes

    def unexpected_backfill(*args, **kwargs):
        raise AssertionError("candidate folder migration ran more than once")

    monkeypatch.setattr(CandidateMixin, "_ensure_folder_records", unexpected_backfill)
    AssetService(tmp_path)
