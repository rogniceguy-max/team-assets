import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.testclient import TestClient
from server import AssetService
from uploads import UploadManager

@pytest.fixture
def upload_env(tmp_path):
    service = AssetService(tmp_path)
    manager = UploadManager(service)
    client = TestClient(Starlette(routes=manager.routes))
    return service, manager, client

def test_chunk_upload_does_not_hold_db_write_lock(upload_env, monkeypatch):
    service, manager, client = upload_env
    data = b"X" * 1024
    session = manager.begin_upload("concurrent.png", len(data))

    orig_copy = shutil.copyfileobj
    started = threading.Barrier(2)
    release = threading.Event()

    def slow_copy(src, dst, length=None):
        started.wait(timeout=5)
        assert release.wait(timeout=5)
        if length is not None:
            return orig_copy(src, dst, length)
        return orig_copy(src, dst)

    monkeypatch.setattr(shutil, "copyfileobj", slow_copy)

    def do_upload():
        return client.put(
            session["relative_path"],
            params={"offset": 0},
            content=data,
            headers={"Authorization": "Bearer " + session["upload_token"]}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        fut = executor.submit(do_upload)
        started.wait(timeout=5)
        try:
            # While chunk is being written to disk in slow_copy, database write MUST succeed
            proj = service.create_project("Project during chunk copy")
            assert proj["name"] == "Project during chunk copy"
            # Another DB write
            proj2 = service.create_project("Second project during chunk copy")
            assert proj2["name"] == "Second project during chunk copy"
        finally:
            release.set()

        resp = fut.result(timeout=5)
        assert resp.status_code == 200
        assert resp.json()["offset"] == len(data)

def test_multi_chunk_data_integrity(upload_env):
    service, manager, client = upload_env
    chunk1 = b"Hello, "
    chunk2 = b"World! "
    chunk3 = b"This is a multi-chunk test."
    full_data = chunk1 + chunk2 + chunk3
    size = len(full_data)

    session = manager.begin_upload("multichunk.bin", size)
    token = session["upload_token"]
    path = session["relative_path"]

    # Send chunk 1
    r1 = client.put(path, params={"offset": 0}, content=chunk1, headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    assert r1.json()["offset"] == len(chunk1)

    # Retry chunk 1 (idempotent)
    r1_retry = client.put(path, params={"offset": 0}, content=chunk1, headers={"Authorization": f"Bearer {token}"})
    assert r1_retry.status_code == 200
    assert r1_retry.json()["offset"] == len(chunk1)

    # Send chunk 2
    r2 = client.put(path, params={"offset": len(chunk1)}, content=chunk2, headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["offset"] == len(chunk1) + len(chunk2)

    # Send chunk 3
    r3 = client.put(path, params={"offset": len(chunk1) + len(chunk2)}, content=chunk3, headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    assert r3.json()["offset"] == size

    # Verify disk content
    target_path = manager.path(session["id"])
    assert target_path.read_bytes() == full_data
