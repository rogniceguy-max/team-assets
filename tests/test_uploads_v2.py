import hashlib
import os
import time
import asyncio
from pathlib import Path

import pytest
import httpx
from fastapi import HTTPException
from PIL import Image
from starlette.applications import Starlette
from starlette.testclient import TestClient

from server import AssetService
from uploads import UploadManager


@pytest.fixture
def upload(tmp_path):
    manager = UploadManager(AssetService(tmp_path))
    return manager, TestClient(Starlette(routes=manager.routes))


def send(client, session, body, offset=0, token=None):
    return client.put(session['relative_path'], params={'offset': offset}, content=body,
                      headers={'Authorization': 'Bearer ' + (token or session['upload_token'])})


def test_chunks_retry_conflict_restart_and_auth(upload):
    manager, client = upload
    session = manager.begin_upload('test.png', 8)
    assert client.get(session['relative_path']).status_code == 401
    assert send(client, session, b'abcd', token='wrong').status_code == 401
    assert send(client, session, b'abcd').json()['offset'] == 4
    assert send(client, session, b'abcd').json()['offset'] == 4
    assert send(client, session, b'zzzz').status_code == 409
    assert send(client, session, b'ef', 5).status_code == 409
    # Simulate a tail written before database commit and a restart.
    with manager.path(session['id']).open('ab') as f:
        f.write(b'crash-tail')
    manager = UploadManager(manager.service)
    client = TestClient(Starlette(routes=manager.routes))
    assert send(client, session, b'efgh', 4).json()['offset'] == 8
    assert manager.path(session['id']).read_bytes() == b'abcdefgh'


def test_concurrent_same_offset_different_chunks_cannot_overwrite(tmp_path):
    async def exercise():
        manager_a = UploadManager(AssetService(tmp_path))
        manager_b = UploadManager(AssetService(tmp_path))
        session = manager_a.begin_upload('race.bin', 4)
        ready = 0
        both_ready = asyncio.Event()

        async def gated_body(body):
            nonlocal ready
            ready += 1
            if ready == 2:
                both_ready.set()
            await both_ready.wait()
            yield body

        app_a = Starlette(routes=manager_a.routes)
        app_b = Starlette(routes=manager_b.routes)
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_a), base_url='http://upload-a') as client_a,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_b), base_url='http://upload-b') as client_b,
        ):
            headers = {'Authorization': 'Bearer ' + session['upload_token']}
            results = await asyncio.gather(
                client_a.put(session['relative_path'], params={'offset': 0}, content=gated_body(b'abcd'), headers=headers),
                client_b.put(session['relative_path'], params={'offset': 0}, content=gated_body(b'wxyz'), headers=headers),
            )
            assert sorted(response.status_code for response in results) == [200, 409]
            winner = b'abcd' if results[0].status_code == 200 else b'wxyz'
            conflict = results[1] if results[0].status_code == 200 else results[0]
            assert conflict.json()['detail']['code'] == 'chunk_conflict'
            assert manager_a.path(session['id']).read_bytes() == winner
            assert manager_b.status(session['id'])['offset'] == 4

            retry = await client_a.put(
                session['relative_path'], params={'offset': 0}, content=winner, headers=headers
            )
            assert retry.status_code == 200
            assert retry.json()['offset'] == 4

    asyncio.run(exercise())


def test_expired_and_size(upload):
    manager, client = upload
    session = manager.begin_upload('a.png', 1)
    with manager.service.connect(write=True) as c:
        c.execute('UPDATE upload_sessions SET expires_at=?', (time.time() - 1,))
    assert send(client, session, b'x').status_code == 410
    session = manager.begin_upload('b.png', 1)
    assert send(client, session, b'xx').status_code == 413
    assert manager.status(session['id'])['offset'] == 0
    manager.cancel_upload(session['id'])
    assert send(client, session, b'x').status_code == 409


def test_prune_cleans_only_stale_known_transient_staging_files(upload):
    manager, _ = upload
    manager.staging_orphan_ttl = 60
    staging = manager.root / "staging"
    stale_names = [
        "abandoned.candidate",
        "abandoned.complete",
        "abandoned.jpg",
        "abandoned.material",
        "abandoned.thumbnail",
    ]
    recent_names = [f"recent-{Path(name).suffix[1:]}{Path(name).suffix}" for name in stale_names]
    old_time = time.time() - manager.staging_orphan_ttl - 1
    stale_paths = []
    recent_paths = []
    for name in stale_names:
        path = staging / name
        path.write_bytes(b"orphan")
        os.utime(path, (old_time, old_time))
        stale_paths.append(path)
    for name in recent_names:
        path = staging / name
        path.write_bytes(b"active")
        recent_paths.append(path)
    unknown_path = staging / "unknown.staging"
    unknown_path.write_bytes(b"preserve")
    os.utime(unknown_path, (old_time, old_time))

    removed = manager.prune_expired_sessions()

    assert all(not path.exists() for path in stale_paths)
    assert all(path.exists() for path in recent_paths)
    assert unknown_path.exists()
    assert all(str(path) in removed for path in stale_paths)


def test_sha_and_invalid_image(upload):
    manager, client = upload
    session = manager.begin_upload('a.png', 4, '0' * 64)
    send(client, session, b'abcd')
    with pytest.raises(HTTPException, match='SHA-256'):
        manager.complete_upload(session['id'])
    other = manager.begin_upload('a.png', 4)
    send(client, other, b'abcd')
    with pytest.raises(HTTPException, match='Invalid image'):
        manager.complete_upload(other['id'])


def test_large_4k_image_stream_dedup_and_idempotency(upload, tmp_path, monkeypatch):
    manager, client = upload
    image_path = tmp_path / '4k.png'
    # Uncompressed RGB PNG: valid 4096 square image larger than 30 MiB.
    Image.new('RGB', (4096, 4096), '#ab12cd').save(image_path, compress_level=0)
    assert image_path.stat().st_size > 30 * 1024 * 1024
    size = image_path.stat().st_size
    digest = hashlib.sha256()
    with image_path.open('rb') as f:
        for chunk in iter(lambda: f.read(manager.CHUNK_SIZE), b''):
            digest.update(chunk)
    session = manager.begin_upload('4k.png', size, digest.hexdigest(), 'once')
    repeated = manager.begin_upload('4k.png', size, digest.hexdigest(), 'once')
    assert repeated['id'] == session['id']
    assert repeated['upload_token'] != session['upload_token']
    session = repeated
    with image_path.open('rb') as f:
        offset = 0
        for chunk in iter(lambda: f.read(manager.CHUNK_SIZE), b''):
            response = send(client, session, chunk, offset)
            assert response.status_code == 200, response.text
            offset += len(chunk)
    # Completion must never read the original through Path.read_bytes.
    monkeypatch.setattr(Path, 'read_bytes', lambda self: pytest.fail('whole-file read'))
    result = manager.complete_upload(session['id'])
    assert result['status'] == 'ready'
    assert manager.complete_upload(session['id']) == result
    with manager.service.connect() as c:
        stored = dict(c.execute('SELECT * FROM files WHERE id=?', (result['file_id'],)).fetchone())
    assert stored['width'] == 4096 and stored['size'] == size
    assert (manager.root / stored['thumbnail']).exists()
    manager.cancel_upload(session['id'])
    assert (manager.root / stored['path']).exists()
    assert manager.capabilities()['max_file_bytes'] == 0
    with pytest.raises(HTTPException) as exc:
        manager.begin_upload('other.png', 9, idempotency_key='once')
    assert exc.value.status_code == 409

def test_small_image_dedup_and_chunk_limit(upload, tmp_path):
    manager, client = upload
    image_path = tmp_path / 'small.png'
    Image.new('RGB', (20, 30), 'red').save(image_path)
    body = image_path.read_bytes()
    results = []
    for _ in range(2):
        session = manager.begin_upload('image.png', len(body))
        assert send(client, session, body).status_code == 200
        results.append(manager.complete_upload(session['id']))
    assert results[0]['file_id'] == results[1]['file_id']
    with manager.service.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1
    session = manager.begin_upload('big.png', manager.CHUNK_SIZE + 1)
    assert send(client, session, b'x' * (manager.CHUNK_SIZE + 1)).status_code == 413
    assert manager.status(session['id'])['offset'] == 0

def test_complete_race_does_not_hold_write_lock(upload, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import uploads
    manager, client = upload
    image_path = tmp_path / 'race.png'
    Image.new('RGB', (64, 64), 'blue').save(image_path)
    body = image_path.read_bytes()
    session = manager.begin_upload('race.png', len(body))
    send(client, session, body)
    original_digest = uploads.digest_file
    started = threading.Barrier(3)
    release = threading.Event()
    def paused_digest(path):
        started.wait(timeout=5)
        assert release.wait(timeout=5)
        return original_digest(path)
    monkeypatch.setattr(uploads, 'digest_file', paused_digest)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(manager.complete_upload, session['id'])
        second = executor.submit(manager.complete_upload, session['id'])
        started.wait(timeout=5)
        try:
            # A write remains possible while both decoders/hashers are in flight.
            assert manager.service.create_project('During completion')['name'] == 'During completion'
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert manager.cancel_upload(session['id'])['status'] == 'ready'
    assert list((manager.root / 'staging').iterdir()) == []


def test_cancel_during_complete_and_failed_registration_cleanup(upload, tmp_path, monkeypatch):
    import uploads
    manager, client = upload
    image_path = tmp_path / 'cancel.png'
    Image.new('RGB', (64, 64), 'blue').save(image_path)
    body = image_path.read_bytes()
    session = manager.begin_upload('cancel.png', len(body))
    send(client, session, body)
    original_digest = uploads.digest_file
    def cancelled_digest(path):
        manager.cancel_upload(session['id'])
        return original_digest(path)
    monkeypatch.setattr(uploads, 'digest_file', cancelled_digest)
    with pytest.raises(HTTPException) as exc:
        manager.complete_upload(session['id'])
    assert exc.value.status_code == 409
    assert list((manager.root / 'staging').iterdir()) == []
    assert list((manager.root / 'originals').iterdir()) == []
    monkeypatch.setattr(uploads, 'digest_file', original_digest)
    session = manager.begin_upload('fail.png', len(body))
    send(client, session, body)
    with manager.service.connect(write=True) as c:
        c.execute("CREATE TRIGGER fail_registration BEFORE INSERT ON files BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    with pytest.raises(Exception, match='simulated failure'):
        manager.complete_upload(session['id'])
    assert list((manager.root / 'originals').iterdir()) == []
    assert list((manager.root / 'thumbnails').iterdir()) == []
    staged = [p for p in (manager.root / 'staging').iterdir() if p.suffix != '.lock']
    assert staged == [manager.path(session['id'])]
    with manager.service.connect(write=True) as c:
        c.execute('DROP TRIGGER fail_registration')
    assert manager.complete_upload(session['id'])['status'] == 'ready'
