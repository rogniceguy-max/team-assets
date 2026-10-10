import os
import time
from pathlib import Path
import pytest
from server import AssetService
from uploads import UploadManager

def test_prune_expired_sessions_and_staging_files(tmp_path):
    service = AssetService(tmp_path)
    manager = UploadManager(service)
    manager.ttl = 3600  # 1 hour TTL for testing

    # 1. Create an active session and file
    active_session = manager.begin_upload("active.png", 1024)
    active_path = manager.path(active_session["id"])
    active_path.write_bytes(b"active data")

    # 2. Create an expired session and file
    expired_session = manager.begin_upload("expired.png", 2048)
    expired_path = manager.path(expired_session["id"])
    expired_path.write_bytes(b"expired data")

    # Mark expired_session as expired in DB
    past_time = time.time() - 100
    with service.connect(write=True) as c:
        c.execute("UPDATE upload_sessions SET expires_at=? WHERE id=?", (past_time, expired_session["id"]))

    # 3. Create orphan staging files (.upload and .chunk) older than TTL
    staging = tmp_path / "staging"
    orphan_upload = staging / "orphan1234567890abcdef12345678.upload"
    orphan_upload.write_bytes(b"orphan upload content")
    orphan_chunk = staging / "chunk1234567890abcdef12345678.chunk"
    orphan_chunk.write_bytes(b"orphan chunk content")

    # Touch orphan files to be older than TTL
    old_mtime = time.time() - 4000
    os.utime(orphan_upload, (old_mtime, old_mtime))
    os.utime(orphan_chunk, (old_mtime, old_mtime))

    # 4. Create a fresh orphan chunk (e.g. upload currently in flight, should NOT be deleted)
    fresh_chunk = staging / "fresh_inflight.chunk"
    fresh_chunk.write_bytes(b"fresh chunk")

    # Run pruning
    removed = manager.prune_expired_sessions()

    # Verify expired session
    with service.connect() as c:
        row = c.execute("SELECT status FROM upload_sessions WHERE id=?", (expired_session["id"],)).fetchone()
        assert row["status"] == "expired"
        active_row = c.execute("SELECT status FROM upload_sessions WHERE id=?", (active_session["id"],)).fetchone()
        assert active_row["status"] == "uploading"

    assert not expired_path.exists()
    assert active_path.exists()
    assert not orphan_upload.exists()
    assert not orphan_chunk.exists()
    assert fresh_chunk.exists()


def test_automatic_pruning_on_begin_upload(tmp_path):
    service = AssetService(tmp_path)
    manager = UploadManager(service)
    manager.ttl = 100

    # Create a session to expire
    session = manager.begin_upload("to_expire.png", 512)
    session_path = manager.path(session["id"])
    session_path.write_bytes(b"to be pruned")

    # Manually expire session
    with service.connect(write=True) as c:
        c.execute("UPDATE upload_sessions SET expires_at=? WHERE id=?", (time.time() - 10, session["id"]))

    # Set last_prune back in time so begin_upload triggers prune
    manager._last_prune = time.time() - 350

    # Call begin_upload
    new_session = manager.begin_upload("new.png", 512)

    # Verify previous session was pruned automatically
    with service.connect() as c:
        row = c.execute("SELECT status FROM upload_sessions WHERE id=?", (session["id"],)).fetchone()
        assert row["status"] == "expired"
    assert not session_path.exists()
