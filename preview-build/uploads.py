"""Resumable image transfer. Session tokens only authorize one staging upload."""
import hashlib
import hmac
import asyncio
import errno
import os
import re
import secrets
import shutil
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from media_utils import IMAGE_FORMATS as FORMATS, inspect_media

VIDEO_FORMATS = ("MP4", "M4V", "MOV", "WEBM", "MKV", "AVI", "MPEG", "MPG", "WMV")


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UploadManager:
    CHUNK_SIZE = 8 * 1024 * 1024

    def __init__(self, service):
        self.service = service
        self.root = service.root
        self.max_file_bytes = int(os.getenv("MAX_FILE_BYTES", "0"))
        self.ttl = int(os.getenv("UPLOAD_TTL_SECONDS", "86400"))
        self.staging_orphan_ttl = int(os.getenv("STAGING_ORPHAN_TTL_SECONDS", "604800"))
        if self.max_file_bytes < 0 or self.ttl < 1 or self.staging_orphan_ttl < 1:
            raise ValueError("Invalid upload configuration")
        (self.root / "staging").mkdir(parents=True, exist_ok=True)
        with service.connect(write=True) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS upload_sessions(
                id TEXT PRIMARY KEY,filename TEXT NOT NULL,size INTEGER NOT NULL,
                expected_sha TEXT,tokenhash TEXT NOT NULL,created_at REAL NOT NULL,
                expires_at REAL NOT NULL,offset INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'uploading',file_id TEXT,
                request_key TEXT UNIQUE, fingerprint TEXT NOT NULL)""")
            c.execute(
                "CREATE INDEX IF NOT EXISTS upload_sessions_status_expiry ON upload_sessions(status,expires_at)"
            )
        self.routes = [Route("/uploads/{upload_id}", self.http, methods=["GET", "PUT"])]
        self._last_prune = time.time()
        self.prune_expired_sessions()

    def prune_expired_sessions(self):
        """Clean up expired upload sessions and uncommitted staging files."""
        now_ts = time.time()
        removed = []
        try:
            with self.service.connect() as c:
                rows = c.execute(
                    "SELECT id FROM upload_sessions WHERE status='uploading' AND expires_at <= ?",
                    (now_ts,)
                ).fetchall()
            for r in rows:
                uid = r["id"]
                try:
                    marked_expired = False
                    with self._session_lock(uid):
                        with self.service.connect(write=True) as c:
                            current = c.execute(
                                "SELECT status,expires_at FROM upload_sessions WHERE id=?", (uid,)
                            ).fetchone()
                            if not current or current["status"] != "uploading" or current["expires_at"] > now_ts:
                                continue
                            target = self.path(uid)
                            if target.exists():
                                target.unlink(missing_ok=True)
                                removed.append(str(target))
                            c.execute("UPDATE upload_sessions SET status='expired' WHERE id=?", (uid,))
                            marked_expired = True
                    if marked_expired:
                        self._remove_session_lock(uid)
                except Exception:
                    pass
            staging = self.root / "staging"
            if staging.exists():
                session_cutoff = now_ts - self.ttl
                orphan_cutoff = now_ts - self.staging_orphan_ttl
                session_suffixes = {".upload", ".chunk"}
                orphan_suffixes = {".candidate", ".complete", ".jpg", ".material", ".thumbnail"}
                for p in staging.iterdir():
                    if not p.is_file():
                        continue
                    cutoff = (
                        session_cutoff if p.suffix in session_suffixes
                        else orphan_cutoff if p.suffix in orphan_suffixes
                        else None
                    )
                    if cutoff is None:
                        continue
                    try:
                        stale = p.stat().st_mtime < cutoff
                        if stale:
                            p.unlink(missing_ok=True)
                            removed.append(str(p))
                    except OSError:
                        # An upload or staging operation may remove the path concurrently.
                        pass
        except Exception:
            pass
        return removed

    def capabilities(self):
        return {"max_file_bytes": self.max_file_bytes, "chunk_size": self.CHUNK_SIZE,
                "upload_ttl_seconds": self.ttl, "resumable": True,
                "formats": list(FORMATS) + ["VIDEO"], "free_disk_bytes": shutil.disk_usage(self.root).free}

    def path(self, upload_id):
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
            raise HTTPException(404, "Upload not found")
        return self.root / "staging" / (upload_id + ".upload")

    def _open_session_lock(self, upload_id):
        self.path(upload_id)
        lock_path = self.root / "staging" / (upload_id + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name == "nt" and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        return descriptor

    def _remove_session_lock(self, upload_id):
        try:
            (self.root / "staging" / (upload_id + ".lock")).unlink(missing_ok=True)
        except OSError:
            # A waiting process may still have the old lock file open on Windows.
            pass

    @staticmethod
    def _try_session_lock(descriptor):
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_session_lock(descriptor):
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _lock_contended(exc):
        return exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)

    @contextmanager
    def _session_lock(self, upload_id):
        """Serialize one upload across worker processes without holding SQLite during file I/O."""
        descriptor = self._open_session_lock(upload_id)
        acquired = False
        try:
            while not acquired:
                try:
                    self._try_session_lock(descriptor)
                    acquired = True
                except OSError as exc:
                    if not self._lock_contended(exc):
                        raise
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                self._unlock_session_lock(descriptor)
            os.close(descriptor)

    @asynccontextmanager
    async def _async_session_lock(self, upload_id):
        descriptor = self._open_session_lock(upload_id)
        acquired = False
        try:
            while not acquired:
                try:
                    self._try_session_lock(descriptor)
                    acquired = True
                except OSError as exc:
                    if not self._lock_contended(exc):
                        raise
                    await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                self._unlock_session_lock(descriptor)
            os.close(descriptor)

    def _row(self, c, upload_id):
        self.path(upload_id)
        row = c.execute("SELECT * FROM upload_sessions WHERE id=?", (upload_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Upload not found")
        return dict(row)

    def _public(self, row):
        return {k: row[k] for k in ("id", "filename", "size", "offset", "status", "file_id", "expires_at")}

    def status(self, upload_id):
        with self.service.connect() as c:
            row = self._row(c, upload_id)
        result = self._public(row)
        result["expired"] = row["expires_at"] <= time.time()
        return result

    def begin_upload(self, filename, size, sha256=None, idempotency_key=None):
        if time.time() - self._last_prune > 300:
            self._last_prune = time.time()
            self.prune_expired_sessions()
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
            raise HTTPException(422, "Filename must have 1–255 characters")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size > 9223372036854775807:
            raise HTTPException(422, "Size must be a positive integer")
        if self.max_file_bytes and size > self.max_file_bytes:
            raise HTTPException(413, "Configured file limit exceeded")
        if sha256 is not None and (not isinstance(sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256)):
            raise HTTPException(422, "Invalid SHA-256")
        sha256 = sha256.lower() if sha256 else None
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise HTTPException(422, "Invalid idempotency key")
        fingerprint = hashlib.sha256(repr((filename, size, sha256)).encode()).hexdigest()
        token = secrets.token_urlsafe(32)
        with self.service.connect(write=True) as c:
            old = c.execute("SELECT * FROM upload_sessions WHERE request_key=?", (idempotency_key,)).fetchone() if idempotency_key else None
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Idempotency key reused for different content")
                row = dict(old)
                c.execute("UPDATE upload_sessions SET tokenhash=? WHERE id=?", (hashlib.sha256(token.encode()).hexdigest(), row["id"]))
            else:
                if size > shutil.disk_usage(self.root).free:
                    raise HTTPException(507, "Insufficient disk space")
                upload_id = uuid.uuid4().hex
                created = time.time()
                c.execute("INSERT INTO upload_sessions(id,filename,size,expected_sha,tokenhash,created_at,expires_at,request_key,fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                          (upload_id, filename.replace('\\', '/').split('/')[-1], size, sha256, hashlib.sha256(token.encode()).hexdigest(), created, created + self.ttl, idempotency_key, fingerprint))
                row = self._row(c, upload_id)
        result = self._public(row)
        result["token"] = token
        result["upload_token"] = token
        result["relative_path"] = f"/uploads/{row['id']}"
        result["upload_url"] = f"/uploads/{row['id']}"
        return result

    def _writable(self, row):
        if row["expires_at"] <= time.time():
            raise HTTPException(410, "Upload expired; please restart")
        if row["status"] != "uploading":
            raise HTTPException(409, f"Upload is already {row['status']}")

    async def http(self, request):
        upload_id = request.path_params["upload_id"]
        temporary = None
        try:
            with self.service.connect() as c:
                row = self._row(c, upload_id)
            provided = request.headers.get("x-upload-token")
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[7:].strip()
            if not provided or not hmac.compare_digest(hashlib.sha256(provided.encode()).hexdigest(), row["tokenhash"]):
                raise HTTPException(401, "Invalid upload token")
            if request.method == "GET":
                return JSONResponse(self.status(upload_id))
            self._writable(row)
            try:
                offset = int(request.query_params["offset"])
                if offset < 0:
                    raise ValueError()
            except (ValueError, KeyError):
                raise HTTPException(422, "A nonnegative offset is required")
            content_length = request.headers.get("content-length")
            if content_length and (not content_length.isdigit() or int(content_length) > self.CHUNK_SIZE):
                raise HTTPException(413, "Chunk exceeds 8 MiB")
            temporary = self.root / "staging" / (uuid.uuid4().hex + ".chunk")
            count = 0
            with temporary.open("wb") as output:
                async for block in request.stream():
                    count += len(block)
                    if count > self.CHUNK_SIZE or offset + count > row["size"]:
                        raise HTTPException(413, "Chunk exceeds declared size or 8 MiB")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if not count:
                raise HTTPException(422, "Empty chunk")

            target = self.path(upload_id)
            async with self._async_session_lock(upload_id):
                with self.service.connect() as c:
                    current_row = self._row(c, upload_id)
                self._writable(current_row)
                if offset < current_row["offset"] and offset + count <= current_row["offset"]:
                    # The accepted prefix is immutable while this per-upload
                    # interprocess lock is held.
                    with target.open("rb") as existing, temporary.open("rb") as incoming:
                        existing.seek(offset)
                        while block := incoming.read(1024 * 1024):
                            if existing.read(len(block)) != block:
                                raise HTTPException(
                                    409, {"code": "chunk_conflict", "offset": current_row["offset"]}
                                )
                    result_row = current_row
                elif offset != current_row["offset"]:
                    raise HTTPException(
                        409, {"code": "offset_conflict", "offset": current_row["offset"]}
                    )
                else:
                    # The request body was staged before taking this lock. Keep
                    # the bounded disk append outside SQLite's global write lock.
                    with target.open("r+b" if target.exists() else "w+b") as output, temporary.open("rb") as incoming:
                        output.truncate(offset)
                        output.seek(offset)
                        shutil.copyfileobj(incoming, output, 1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    with self.service.connect(write=True) as c:
                        latest = self._row(c, upload_id)
                        self._writable(latest)
                        if latest["offset"] != offset:
                            raise HTTPException(
                                409, {"code": "offset_conflict", "offset": latest["offset"]}
                            )
                        changed = c.execute(
                            "UPDATE upload_sessions SET offset=? WHERE id=? AND offset=?",
                            (offset + count, upload_id, offset),
                        ).rowcount
                        if changed != 1:
                            raise HTTPException(
                                409, {"code": "offset_conflict", "offset": latest["offset"]}
                            )
                        result_row = self._row(c, upload_id)
            return JSONResponse(self._public(result_row))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except OSError:
            return JSONResponse({"detail": "Upload storage write failed; query offset before retrying"}, status_code=507)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def complete_upload(self, upload_id):
        path = self.path(upload_id)
        snapshot = self.root / "staging" / (uuid.uuid4().hex + ".complete")
        temporary_thumb = self.root / "staging" / (uuid.uuid4().hex + ".jpg")
        created_paths = []
        committed = False
        terminal = False
        try:
            # A hard link pins this immutable, fully uploaded file across concurrent
            # cancellation/completion. No image decode, hash or bulk copy under DB lock.
            with self._session_lock(upload_id):
                with self.service.connect(write=True) as c:
                    row = self._row(c, upload_id)
                    if row["status"] == "ready":
                        terminal = True
                        return self._public(row)
                    self._writable(row)
                    if row["offset"] != row["size"]:
                        raise HTTPException(409, {"code": "upload_incomplete", "offset": row["offset"], "size": row["size"]})
                    with path.open("r+b") as stream:
                        stream.truncate(row["size"])
                    os.link(path, snapshot)
            digest = digest_file(snapshot)
            if row["expected_sha"] and digest != row["expected_sha"]:
                raise HTTPException(422, "SHA-256 mismatch; cancel and restart upload")
            try:
                media = inspect_media(snapshot, row["filename"], None, temporary_thumb)
            except ValueError as exc:
                raise HTTPException(422, "Invalid image, video, excessive pixel dimensions, or storage error") from exc
            with self.service.connect(write=True) as c:
                current = self._row(c, upload_id)
                if current["status"] == "ready":
                    terminal = True
                    return self._public(current)
                self._writable(current)
                if current["offset"] != row["size"] or current["size"] != row["size"]:
                    raise HTTPException(409, "Upload changed during completion")
                existing = c.execute("SELECT * FROM files WHERE sha256=?", (digest,)).fetchone()
                if existing:
                    file_id = existing["id"]
                else:
                    file_id = uuid.uuid4().hex
                    ext, mime = media["ext"], media["mime"]
                    original, thumb = f"originals/{file_id}.{ext}", f"thumbnails/{file_id}.jpg"
                    snapshot.replace(self.root / original)
                    created_paths.append(self.root / original)
                    temporary_thumb.replace(self.root / thumb)
                    created_paths.append(self.root / thumb)
                    c.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (file_id, digest, original, thumb, mime, row["size"], media["width"], media["height"]))
                c.execute("UPDATE upload_sessions SET status='ready',file_id=? WHERE id=?", (file_id, upload_id))
                result = self._public(self._row(c, upload_id))
            committed = True
            path.unlink(missing_ok=True)
            return result
        except OSError as exc:
            raise HTTPException(422, "Invalid image, excessive pixel dimensions, or storage error") from exc
        finally:
            if committed or terminal:
                self._remove_session_lock(upload_id)
            if not committed:
                for created in created_paths:
                    created.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
            temporary_thumb.unlink(missing_ok=True)

    def cancel_upload(self, upload_id):
        with self._session_lock(upload_id):
            with self.service.connect(write=True) as c:
                row = self._row(c, upload_id)
                if row["status"] == "ready":
                    result = self._public(row)
                else:
                    c.execute("UPDATE upload_sessions SET status='cancelled' WHERE id=?", (upload_id,))
                    result = self._public(self._row(c, upload_id))
                    self.path(upload_id).unlink(missing_ok=True)
        self._remove_session_lock(upload_id)
        return result
