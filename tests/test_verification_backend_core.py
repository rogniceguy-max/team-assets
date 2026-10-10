"""Comprehensive isolated test suite verifying fixes for Issues 3, 9, 10, 11, and 12.

Can be run via:
    pytest tests/test_verification_backend_core.py
or directly:
    python tests/test_verification_backend_core.py
"""

import io
import os
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

# Ensure project root is in sys.path when running standalone
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi import HTTPException
from PIL import Image

from server import AssetService, uid, now
from intake import IntakeService


def make_png(color="blue", size=(32, 32)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class MidStreamError(Exception):
    pass


class FailingStream:
    """Streams one chunk of data and then fails midway."""
    def __init__(self, first_chunk=b"PNG_DATA_MIDWAY_FAILURE"):
        self.first_chunk = first_chunk
        self.called = False

    def read(self, size=-1):
        if not self.called:
            self.called = True
            return self.first_chunk
        raise MidStreamError("Stream abruptly interrupted midway during reading")


# ==============================================================================
# Issue 3: Folder rename exact prefix matching (`A_` vs `A`).
# Renaming `A` to `B` does NOT affect `A_sub`.
# ==============================================================================
def test_issue_3_folder_rename_exact_prefix_matching(tmp_path=None):
    cleanup = False
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir)
        cleanup = True
    try:
        service = AssetService(tmp_path)
        project = service.state()["projects"][0]
        project_id = project["id"]

        # Upload files into folder structures:
        # A/file1.png
        # A/sub/file2.png
        # A_sub/file3.png
        # A_sub/nested/file4.png
        # A_suffix/file5.png
        uploads = [
            ("A/file1.png", io.BytesIO(make_png("red")), "image/png"),
            ("A/sub/file2.png", io.BytesIO(make_png("green")), "image/png"),
            ("A_sub/file3.png", io.BytesIO(make_png("blue")), "image/png"),
            ("A_sub/nested/file4.png", io.BytesIO(make_png("yellow")), "image/png"),
            ("A_suffix/file5.png", io.BytesIO(make_png("purple")), "image/png"),
        ]
        service.add_candidate_uploads(project_id, uploads)

        # Retrieve all candidate folders
        with service.connect() as c:
            folders_before = {r["path"]: dict(r) for r in c.execute(
                "SELECT * FROM candidate_folders WHERE project_id=?", (project_id,)
            ).fetchall()}

        assert "A" in folders_before, "Folder 'A' should exist"
        assert "A/sub" in folders_before, "Folder 'A/sub' should exist"
        assert "A_sub" in folders_before, "Folder 'A_sub' should exist"
        assert "A_sub/nested" in folders_before, "Folder 'A_sub/nested' should exist"
        assert "A_suffix" in folders_before, "Folder 'A_suffix' should exist"

        folder_a = folders_before["A"]

        # Rename folder 'A' -> 'B'
        updated = service.update_candidate_folder(folder_a["id"], folder_a["version"], name="B")
        assert updated["name"] == "B"
        assert updated["path"] == "B"

        # Check candidate folders in database after rename
        with service.connect() as c:
            folders_after = {r["path"]: dict(r) for r in c.execute(
                "SELECT * FROM candidate_folders WHERE project_id=?", (project_id,)
            ).fetchall()}
            candidates_after = {r["name"]: dict(r) for r in c.execute(
                "SELECT * FROM candidates WHERE project_id=? AND deleted_at IS NULL", (project_id,)
            ).fetchall()}

        # 1. 'A' renamed to 'B'
        assert "A" not in folders_after, "Old folder 'A' must no longer exist"
        assert "B" in folders_after, "New folder 'B' must exist"
        assert folders_after["B"]["name"] == "B"

        # 2. Descendant 'A/sub' renamed to 'B/sub'
        assert "A/sub" not in folders_after, "Old descendant 'A/sub' must no longer exist"
        assert "B/sub" in folders_after, "Descendant 'B/sub' must exist"
        assert folders_after["B/sub"]["name"] == "sub"
        assert folders_after["B/sub"]["parent_path"] == "B"

        # 3. CRITICAL: 'A_sub' and 'A_sub/nested' and 'A_suffix' MUST NOT BE AFFECTED
        assert "A_sub" in folders_after, "Folder 'A_sub' must remain untouched"
        assert folders_after["A_sub"]["name"] == "A_sub"
        assert folders_after["A_sub"]["parent_path"] == ""

        assert "A_sub/nested" in folders_after, "Folder 'A_sub/nested' must remain untouched"
        assert folders_after["A_sub/nested"]["name"] == "nested"
        assert folders_after["A_sub/nested"]["parent_path"] == "A_sub"

        assert "A_suffix" in folders_after, "Folder 'A_suffix' must remain untouched"
        assert folders_after["A_suffix"]["name"] == "A_suffix"

        # 4. No unintended folders like 'B_sub' or 'B_suffix' created
        assert "B_sub" not in folders_after, "'B_sub' should not exist"
        assert "B_suffix" not in folders_after, "'B_suffix' should not exist"

        # 5. Check candidates paths
        assert candidates_after["file1.png"]["folder_path"] == "B"
        assert candidates_after["file1.png"]["relative_path"] == "B/file1.png"

        assert candidates_after["file2.png"]["folder_path"] == "B/sub"
        assert candidates_after["file2.png"]["relative_path"] == "B/sub/file2.png"

        assert candidates_after["file3.png"]["folder_path"] == "A_sub", "file3.png folder_path must remain A_sub"
        assert candidates_after["file3.png"]["relative_path"] == "A_sub/file3.png"

        assert candidates_after["file4.png"]["folder_path"] == "A_sub/nested", "file4.png folder_path must remain A_sub/nested"
        assert candidates_after["file4.png"]["relative_path"] == "A_sub/nested/file4.png"

        assert candidates_after["file5.png"]["folder_path"] == "A_suffix", "file5.png folder_path must remain A_suffix"
        assert candidates_after["file5.png"]["relative_path"] == "A_suffix/file5.png"

    finally:
        if cleanup:
            shutil.rmtree(tmp_path, ignore_errors=True)


# ==============================================================================
# Issue 9: `restore_material` returns 409 `CANDIDATE_ALREADY_REFERENCED` if
# active reference exists; `restore_entity` filters conflicting candidate
# references without SQLite unique constraint crashes.
# ==============================================================================
def test_issue_9_restore_material_conflict_and_restore_entity_filter(tmp_path=None):
    cleanup = False
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir)
        cleanup = True
    try:
        service = AssetService(tmp_path)
        project = service.state()["projects"][0]
        project_id = project["id"]

        asset = service.create_asset(project_id, "测试资产A")
        asset_id = asset["id"]
        v1 = service.create_variant(asset_id, "变体1")
        v2 = service.create_variant(asset_id, "变体2")

        # Upload candidate C1
        c1 = service.add_candidate_uploads(
            project_id, [("candidate1.png", io.BytesIO(make_png("cyan")), "image/png")]
        )["candidates"][0]

        # ----------------------------------------------------------------------
        # Part 1: Verify restore_material returns 409 CANDIDATE_ALREADY_REFERENCED
        # ----------------------------------------------------------------------
        # Reference C1 to asset under variant v1 -> creates material M1
        ref1 = service.create_candidate_reference(c1["id"], asset_id, variant_id=v1["id"])
        m1 = ref1["material"]
        assert m1["candidate_id"] == c1["id"]
        assert m1["asset_id"] == asset_id
        assert m1["deleted_at"] is None

        # Delete material M1
        service.delete_material(m1["id"])
        with service.connect() as conn:
            m1_row = conn.execute("SELECT * FROM materials WHERE id=?", (m1["id"],)).fetchone()
            assert m1_row["deleted_at"] is not None

        # Now reference C1 again to asset under variant v2 -> creates active material M2
        ref2 = service.create_candidate_reference(c1["id"], asset_id, variant_id=v2["id"])
        m2 = ref2["material"]
        assert m2["id"] != m1["id"]
        assert m2["candidate_id"] == c1["id"]
        assert m2["deleted_at"] is None

        # Attempting to restore M1 while M2 is active must raise 409 CANDIDATE_ALREADY_REFERENCED
        with pytest.raises(HTTPException) as exc_info:
            service.restore_material(m1["id"])

        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("code") == "CANDIDATE_ALREADY_REFERENCED"
        assert detail.get("conflict_id") == m2["id"]

        # ----------------------------------------------------------------------
        # Part 2: Verify restore_entity filters conflicting candidate references
        # without SQLite unique constraint crashes
        # ----------------------------------------------------------------------
        # Create a second asset and candidate to test entity restoration
        asset2 = service.create_asset(project_id, "测试资产B")
        asset2_id = asset2["id"]

        c2 = service.add_candidate_uploads(
            project_id, [("candidate2.png", io.BytesIO(make_png("magenta")), "image/png")]
        )["candidates"][0]

        ref_orig = service.create_candidate_reference(c2["id"], asset2_id)
        m_orig = ref_orig["material"]

        # Delete asset2 -> soft deletes asset2 and m_orig with delete_batch
        del_result = service.delete_entity("asset", asset2_id, version=asset2["version"])
        assert del_result["ok"] is True
        batch_id = del_result["batch_id"]

        with service.connect() as conn:
            m_orig_row = conn.execute("SELECT * FROM materials WHERE id=?", (m_orig["id"],)).fetchone()
            assert m_orig_row["deleted_at"] is not None
            assert m_orig_row["delete_batch"] == batch_id

            # Directly insert an active material referencing c2 for asset2 to create a conflict state
            # (e.g. concurrent creation or re-reference)
            m_active_id = uid()
            conn.execute(
                "INSERT INTO materials(id,variant_id,name,source,file_id,created_at,deleted_at,delete_batch,asset_id,candidate_id) "
                "VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                (m_active_id, None, "active.png", "candidate", c2["file_id"], now(), asset2_id, c2["id"])
            )

        # Restoring asset2:
        # Without fix: violates SQLite partial index 'materials_candidate_asset' and crashes
        # With fix: filters out m_orig, restores asset2, affected materials count excludes m_orig
        restore_result = service.restore_entity("asset", asset2_id)
        assert restore_result["ok"] is True
        assert restore_result["entity"]["id"] == asset2_id
        assert restore_result["entity"]["deleted_at"] is None

        # Verify m_orig was filtered out and remained deleted, while m_active remained active
        with service.connect() as conn:
            m_orig_check = conn.execute("SELECT deleted_at FROM materials WHERE id=?", (m_orig["id"],)).fetchone()
            assert m_orig_check["deleted_at"] is not None, "Conflicting material must remain deleted"

            m_active_check = conn.execute("SELECT deleted_at FROM materials WHERE id=?", (m_active_id,)).fetchone()
            assert m_active_check["deleted_at"] is None, "Active material must remain active"

    finally:
        if cleanup:
            shutil.rmtree(tmp_path, ignore_errors=True)


# ==============================================================================
# Issue 10: Concurrent `apply_project_import` with same asset name strictly
# enforces `on_name_conflict="error"` inside write transaction; verify with
# concurrent threads.
# ==============================================================================
def test_issue_10_concurrent_apply_project_import_name_conflict(tmp_path=None):
    cleanup = False
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir)
        cleanup = True
    try:
        service = AssetService(tmp_path)
        intake = IntakeService(service)
        project = service.state()["projects"][0]
        project_id = project["id"]

        target_name = "并发唯一英雄"
        manifest_assets = [{
            "name": target_name,
            "category": "人物",
            "role": "主角",
            "variants": [{"name": "默认形态", "episodes": "1-5", "description": "核心描述"}],
        }]

        num_threads = 4
        # We synchronize threads right after _normalize_project_manifest passes
        # to guarantee all threads pass validation before any enters the write transaction.
        # This rigorously tests that the write transaction check strictly enforces
        # on_name_conflict="error" (raising 409 ASSET_NAME_EXISTS) when racing.
        orig_normalize = intake._normalize_project_manifest
        norm_barrier = threading.Barrier(num_threads)

        def synchronized_normalize(*args, **kwargs):
            result = orig_normalize(*args, **kwargs)
            norm_barrier.wait(timeout=10)
            return result

        intake._normalize_project_manifest = synchronized_normalize

        results = []
        exceptions = []

        def worker(thread_idx):
            # Unique idempotency_key for each thread
            key = f"concurrent-test-key-{thread_idx}"
            try:
                res = intake.apply_project_import(
                    project_id,
                    manifest_assets,
                    idempotency_key=key,
                    on_name_conflict="error",
                )
                results.append((thread_idx, res))
            except Exception as e:
                exceptions.append((thread_idx, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly ONE thread must have successfully created the asset
        assert len(results) == 1, f"Expected exactly 1 successful import, got {len(results)}"
        success_idx, success_res = results[0]
        assert success_res["assets"][0]["name"] == target_name

        # All other threads were inside the write transaction check and must receive 409 ASSET_NAME_EXISTS
        assert len(exceptions) == num_threads - 1, f"Expected {num_threads - 1} conflicts, got {len(exceptions)}"
        for idx, exc in exceptions:
            assert isinstance(exc, HTTPException), f"Thread {idx} got unexpected exception: {type(exc)}: {exc}"
            assert exc.status_code == 409, f"Thread {idx} got status {exc.status_code}, expected 409"
            detail = exc.detail
            assert isinstance(detail, dict)
            assert detail.get("code") == "ASSET_NAME_EXISTS", f"Thread {idx} got error code: {detail.get('code')}"
            assert detail.get("name") == target_name

        # Check DB: exactly 1 asset with this name must exist in the project
        with service.connect() as c:
            db_assets = c.execute(
                "SELECT id, name FROM assets WHERE project_id=? AND name=? AND deleted_at IS NULL",
                (project_id, target_name),
            ).fetchall()
            assert len(db_assets) == 1, f"Expected exactly 1 asset in DB, found {len(db_assets)}"

    finally:
        if cleanup:
            shutil.rmtree(tmp_path, ignore_errors=True)


# ==============================================================================
# Issue 11: `_normalize_project_manifest` validates field types (category length,
# role <=1000, description non-string/<=20000, material_names string dict).
# ==============================================================================
def test_issue_11_normalize_project_manifest_type_validations(tmp_path=None):
    cleanup = False
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir)
        cleanup = True
    try:
        service = AssetService(tmp_path)
        intake = IntakeService(service)
        project = service.state()["projects"][0]
        project_id = project["id"]

        # Upload a test file so we have a valid file_id
        upload = service.add_candidate_uploads(
            project_id, [("test_valid.png", io.BytesIO(make_png("white")), "image/png")]
        )["candidates"][0]
        valid_file_id = upload["file_id"]

        # Helper to extract error codes
        def get_error_codes(manifest_assets):
            norm, errors, warnings = intake._normalize_project_manifest(project_id, manifest_assets)
            return [e.get("code") for e in errors]

        # 1. Category validation:
        # Non-string, empty/whitespace, > 100 chars -> INVALID_ASSET_CATEGORY
        bad_categories = [
            123,
            True,
            ["list_cat"],
            "",
            "   ",
            "C" * 101,
        ]
        for bad_cat in bad_categories:
            manifest = [{
                "name": "CatTest",
                "category": bad_cat,
                "variants": [{"name": "V1", "episodes": "1"}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_ASSET_CATEGORY" in codes, f"Expected INVALID_ASSET_CATEGORY for category: {repr(bad_cat)}"

        # Valid category values:
        for valid_cat in ["道具", "C" * 100, None]:
            manifest = [{
                "name": "CatTestValid",
                "category": valid_cat,
                "variants": [{"name": "V1", "episodes": "1"}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_ASSET_CATEGORY" not in codes, f"Unexpected error for valid category: {repr(valid_cat)}"

        # 2. Role validation:
        # Non-string, > 1000 chars -> INVALID_ASSET_ROLE
        bad_roles = [
            12345,
            False,
            {"not": "string"},
            "R" * 1001,
        ]
        for bad_role in bad_roles:
            manifest = [{
                "name": "RoleTest",
                "role": bad_role,
                "variants": [{"name": "V1", "episodes": "1"}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_ASSET_ROLE" in codes, f"Expected INVALID_ASSET_ROLE for role: {repr(bad_role)}"

        # Valid role values:
        for valid_role in ["主角设定", "R" * 1000, "", None]:
            manifest = [{
                "name": "RoleTestValid",
                "role": valid_role,
                "variants": [{"name": "V1", "episodes": "1"}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_ASSET_ROLE" not in codes, f"Unexpected error for valid role: {repr(valid_role)}"

        # 3. Description validation:
        # Non-string, > 20000 chars -> INVALID_VARIANT_DESCRIPTION
        bad_descriptions = [
            99999,
            True,
            ["desc"],
            {"key": "val"},
            "D" * 20001,
        ]
        for bad_desc in bad_descriptions:
            manifest = [{
                "name": "DescTest",
                "variants": [{"name": "V1", "episodes": "1", "description": bad_desc}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_VARIANT_DESCRIPTION" in codes, f"Expected INVALID_VARIANT_DESCRIPTION for description: {repr(bad_desc)}"

        # Valid description values:
        for valid_desc in ["变体描述文本", "D" * 20000, "", None]:
            manifest = [{
                "name": "DescTestValid",
                "variants": [{"name": "V1", "episodes": "1", "description": valid_desc}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_VARIANT_DESCRIPTION" not in codes, f"Unexpected error for valid description: {repr(valid_desc)}"

        # 4. Material_names validation:
        # Non-dict, keys not in file_ids, value non-string, value empty, value > 255 chars
        # 4a. Non-dict material_names:
        for bad_mat_names in ["not_a_dict", [valid_file_id], 123]:
            manifest = [{
                "name": "MatTest1",
                "variants": [{"name": "V1", "episodes": "1", "file_ids": [valid_file_id], "material_names": bad_mat_names}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_MATERIAL_NAMES" in codes, f"Expected INVALID_MATERIAL_NAMES for non-dict: {repr(bad_mat_names)}"

        # 4b. Keys not in file_ids:
        manifest = [{
            "name": "MatTest2",
            "variants": [{"name": "V1", "episodes": "1", "file_ids": [valid_file_id], "material_names": {"unrelated_id": "name.png"}}],
        }]
        codes = get_error_codes(manifest)
        assert "INVALID_MATERIAL_NAMES" in codes, "Expected INVALID_MATERIAL_NAMES for key not in file_ids"

        # 4c. Value not string / whitespace / > 255 chars -> INVALID_MATERIAL_NAME
        bad_name_values = [
            123,
            True,
            "",
            "   ",
            "N" * 256,
        ]
        for bad_val in bad_name_values:
            manifest = [{
                "name": "MatTest3",
                "variants": [{"name": "V1", "episodes": "1", "file_ids": [valid_file_id], "material_names": {valid_file_id: bad_val}}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_MATERIAL_NAME" in codes, f"Expected INVALID_MATERIAL_NAME for value: {repr(bad_val)}"

        # 4d. Valid material_names:
        for valid_val in ["valid_image.png", "N" * 255]:
            manifest = [{
                "name": "MatTestValid",
                "variants": [{"name": "V1", "episodes": "1", "file_ids": [valid_file_id], "material_names": {valid_file_id: valid_val}}],
            }]
            codes = get_error_codes(manifest)
            assert "INVALID_MATERIAL_NAME" not in codes
            assert "INVALID_MATERIAL_NAMES" not in codes

    finally:
        if cleanup:
            shutil.rmtree(tmp_path, ignore_errors=True)


# ==============================================================================
# Issue 12: `add_candidate_uploads` cleans up temporary `.candidate` files
# even when write/fsync/read fails midway.
# ==============================================================================
def test_issue_12_add_candidate_uploads_cleans_up_temp_files(tmp_path=None):
    cleanup = False
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir)
        cleanup = True
    try:
        service = AssetService(tmp_path)
        project = service.state()["projects"][0]
        project_id = project["id"]
        staging = service.root / "staging"

        def get_stray_candidate_files():
            return list(staging.glob("*.candidate"))

        # Scenario 1: stream.read fails midway
        assert get_stray_candidate_files() == []
        failing_stream = FailingStream(first_chunk=b"A" * 65536)
        with pytest.raises(MidStreamError):
            service.add_candidate_uploads(
                project_id,
                [("fail_stream.png", failing_stream, "image/png")]
            )
        assert get_stray_candidate_files() == [], "Orphaned .candidate files detected after stream.read failure!"

        # Scenario 2: os.fsync fails
        assert get_stray_candidate_files() == []
        with patch("os.fsync", side_effect=OSError(5, "Simulated disk fsync I/O failure")):
            with pytest.raises(HTTPException) as exc_info:
                service.add_candidate_uploads(
                    project_id,
                    [("fsync_fail.png", io.BytesIO(make_png()), "image/png")]
                )
            assert exc_info.value.status_code == 507
        assert get_stray_candidate_files() == [], "Orphaned .candidate files detected after os.fsync failure!"

        # Scenario 3: inspect_media / bad image bytes
        assert get_stray_candidate_files() == []
        with pytest.raises(HTTPException) as exc_info:
            service.add_candidate_uploads(
                project_id,
                [("corrupt.png", io.BytesIO(b"CORRUPT_PAYLOAD_NOT_IMAGE"), "image/png")]
            )
        assert exc_info.value.status_code == 422
        assert get_stray_candidate_files() == [], "Orphaned .candidate files detected after inspect_media failure!"

        # Scenario 4: Multi-file upload where first file succeeds and second fails midway
        assert get_stray_candidate_files() == []
        multi_uploads = [
            ("first_valid.png", io.BytesIO(make_png("cyan")), "image/png"),
            ("second_fails.png", FailingStream(first_chunk=b"PARTIAL_CONTENT"), "image/png"),
        ]
        with pytest.raises(MidStreamError):
            service.add_candidate_uploads(project_id, multi_uploads)
        assert get_stray_candidate_files() == [], "Orphaned .candidate files detected after multi-file partial failure!"
        assert list(staging.glob("*.jpg")) == [], "Orphaned .jpg thumbnail staging files detected!"

        # Scenario 5: Successful upload properly cleans up staging too
        valid_uploads = [
            ("valid1.png", io.BytesIO(make_png("red")), "image/png"),
            ("valid2.png", io.BytesIO(make_png("green")), "image/png"),
        ]
        res = service.add_candidate_uploads(project_id, valid_uploads)
        assert len(res["candidates"]) == 2
        assert get_stray_candidate_files() == [], "Temporary .candidate files should be cleaned up on success"
        assert list(staging.glob("*.jpg")) == [], "Staging thumbnails should be cleaned up on success"

    finally:
        if cleanup:
            shutil.rmtree(tmp_path, ignore_errors=True)


# ==============================================================================
# Standalone execution runner
# ==============================================================================
def run_all_tests():
    tests = [
        ("Issue 3: Folder rename exact prefix matching (`A_` vs `A`)", test_issue_3_folder_rename_exact_prefix_matching),
        ("Issue 9: `restore_material` 409 conflict and `restore_entity` filtering", test_issue_9_restore_material_conflict_and_restore_entity_filter),
        ("Issue 10: Concurrent `apply_project_import` name conflict enforcement", test_issue_10_concurrent_apply_project_import_name_conflict),
        ("Issue 11: `_normalize_project_manifest` field type validations", test_issue_11_normalize_project_manifest_type_validations),
        ("Issue 12: `add_candidate_uploads` temporary `.candidate` file cleanup", test_issue_12_add_candidate_uploads_cleans_up_temp_files),
    ]

    print("=" * 80)
    print("BACKEND CORE VERIFICATION SUITE: ISSUES 3, 9, 10, 11, 12")
    print("=" * 80)

    all_passed = True
    for title, test_func in tests:
        try:
            test_func()
            print(f"[PASS] {title}")
        except Exception as e:
            all_passed = False
            print(f"[FAIL] {title}")
            import traceback
            traceback.print_exc()

    print("=" * 80)
    if all_passed:
        print("OVERALL RESULT: ALL 5 ISSUES VERIFIED SUCCESSFULLY [PASS]")
    else:
        print("OVERALL RESULT: SOME ISSUES FAILED [FAIL]")
    print("=" * 80)
    return all_passed


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
