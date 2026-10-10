"""Chunked uploading, file hashing, and candidate/asset CLI ingestion."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .config import RETRYABLE_HTTP_STATUS
from .transport import McpError, _HttpFailure, _backoff


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _progress(message: str, *, end: bool = False) -> None:
    if not sys.stderr.isatty():
        return
    print("\r" + message + ("\n" if end else ""), end="", file=sys.stderr, flush=True)


class UploaderMixin:
    """Chunked resilient upload client implementation."""

    def upload_file(
        self,
        path: str | Path,
        *,
        idempotency_key: str,
        progress: bool = True,
    ) -> dict[str, Any]:
        """Upload one local file and return ``complete_upload``'s file record."""
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ValueError(f"文件不存在：{file_path}")
        size = file_path.stat().st_size
        if size <= 0:
            raise ValueError("不能上传空文件")
        digest = _sha256_file(file_path)
        filename = file_path.name
        begin_args = {"filename": filename, "size": size, "sha256": digest, "idempotency_key": idempotency_key}
        session = self.call_tool("begin_upload", begin_args)
        if not isinstance(session, dict):
            raise McpError("begin_upload 返回格式不正确", payload=session)
        if session.get("status") == "ready" and session.get("file_id"):
            return session
        upload_id = session.get("id")
        upload_token = session.get("upload_token")
        if not upload_id or not upload_token or not session.get("relative_path"):
            raise McpError("begin_upload 缺少上传会话字段", payload=session)
        upload_url = self._upload_url(str(session.get("upload_url") or session["relative_path"]))
        chunk_size = int(session.get("chunk_size") or 8 * 1024 * 1024)
        offset = int(session.get("offset") or 0)
        with file_path.open("rb") as stream:
            while offset < size:
                stream.seek(offset)
                body = stream.read(min(chunk_size, size - offset))
                if not body:
                    raise McpError("读取上传文件时提前到达文件末尾")
                for attempt in range(self.retries + 1):
                    try:
                        response = self.put_chunk(upload_url, str(upload_token), offset, body)
                        next_offset = int(response.get("offset", offset + len(body)))
                        if next_offset < offset or next_offset > size:
                            raise McpError("服务器返回了无效上传 offset", payload=response)
                        offset = next_offset
                        break
                    except _HttpFailure as exc:
                        if exc.status in (401, 403):
                            session = self.call_tool("begin_upload", begin_args)
                            upload_token = session.get("upload_token")
                            upload_url = self._upload_url(str(session.get("upload_url") or session["relative_path"]))
                            offset = int(session.get("offset") or offset)
                            stream.seek(offset)
                            body = stream.read(min(chunk_size, size - offset))
                            continue
                        if exc.status == 409 and isinstance(exc.payload, dict):
                            detail = exc.payload.get("detail", exc.payload)
                            remote_offset = detail.get("offset") if isinstance(detail, dict) else None
                            if remote_offset is not None:
                                offset = int(remote_offset)
                                stream.seek(offset)
                                body = stream.read(min(chunk_size, size - offset))
                                continue
                        if attempt < self.retries and (exc.status in RETRYABLE_HTTP_STATUS or exc.status is None):
                            _backoff(attempt)
                            continue
                        raise
                    except McpError:
                        raise
                else:
                    raise McpError("上传块重试次数已用尽")
                if progress:
                    _progress(f"上传 {offset}/{size}")
        if progress:
            _progress(f"上传 {size}/{size}", end=True)
        return self.call_tool("complete_upload", {"upload_id": upload_id})


def _upload_command(client: Any, args: argparse.Namespace) -> Any:
    path = Path(args.file).expanduser()
    file_stat = path.stat()
    name = args.name or path.name
    folder_path = getattr(args, "folder_path", None)
    file_relative_path = getattr(args, "file_relative_path", None)
    file_key = f"{path.name}:{file_stat.st_mtime_ns}:{file_stat.st_size}"
    upload_key = f"upload:{args.idempotency_key or file_key}"[:200]
    if args.mode == "candidate" and folder_path and file_relative_path:
        raise ValueError("--folder-path 与 --file-relative-path 不能同时提供")
    file_relative_parts = []
    if args.mode == "candidate" and file_relative_path:
        rel = str(file_relative_path).replace("\\", "/").strip().strip("/")
        file_relative_parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if not file_relative_parts or file_relative_parts[-1] != name:
            raise ValueError("--file-relative-path 的末级必须与最终文件名相同")
    completed = client.upload_file(path, idempotency_key=upload_key)
    file_id = completed.get("file_id")
    if not file_id:
        raise McpError("complete_upload 未返回 file_id", payload=completed)
    if args.mode == "candidate":
        register = {
            "project_id": args.project_id,
            "file_id": file_id,
            "filename": name,
            "category": args.category,
        }
        if folder_path:
            rel = str(folder_path).replace("\\", "/").strip().strip("/")
            if rel:
                register["folder_path"] = "/".join(p for p in rel.split("/") if p not in ("", ".", ".."))
        elif file_relative_parts:
            register["relative_path"] = "/".join(file_relative_parts)
        return {"upload": completed, "candidate": client.call_tool("register_candidate", register)}

    if not args.project_id:
        raise ValueError("asset 模式需要 --project-id")
    if not args.asset_id and not args.asset_name:
        raise ValueError("asset 模式需要 --asset-id 或 --asset-name")
    if not args.variant_id and not args.variant_name:
        raise ValueError("asset 模式需要 --variant-id 或 --variant-name")
    variant: dict[str, Any] = {"file_ids": [file_id], "material_names": {file_id: name}}
    if args.variant_id:
        variant["variant_id"] = args.variant_id
    else:
        variant.update({"name": args.variant_name, "episodes": args.episodes, "description": args.description})
    commit: dict[str, Any] = {
        "project_id": args.project_id,
        "variants": [variant],
    }
    if args.asset_id:
        commit["asset_id"] = args.asset_id
    else:
        commit["asset"] = {"name": args.asset_name, "category": args.category, "role": args.role}

    target_payload = {
        "project_id": args.project_id,
        "asset_id": args.asset_id,
        "asset": commit.get("asset"),
        "variants": commit.get("variants"),
    }
    target_hash = hashlib.sha256(
        json.dumps(target_payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    base_key = (args.idempotency_key or f"team-assets-cli:{file_key}")[:160]
    commit["idempotency_key"] = f"{base_key}:intake:{target_hash}"

    return {"upload": completed, "intake": client.call_tool("commit_intake", commit)}
