"""Client auto-update, SHA-256 verification, downgrade prevention, and safe rollback."""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import RETRYABLE_HTTP_STATUS
from .transport import McpError, _HttpFailure, _backoff, _decode_response, _error_message, _retry_after


class UpdaterMixin:
    """Client package download, verification, and staged directory replacement."""

    def download_client_package(self, url: str) -> bytes:
        """Download the server's versioned client zip using the optional bearer header."""
        headers = self._headers()
        headers["Accept"] = "application/zip, application/octet-stream"
        attempts = self.retries
        for attempt in range(attempts + 1):
            request = Request(url, headers=headers, method="GET")
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except HTTPError as exc:
                raw = exc.read()
                payload = _decode_response(raw, exc.headers.get("Content-Type", ""))
                if exc.code in RETRYABLE_HTTP_STATUS and attempt < attempts:
                    _backoff(attempt, _retry_after(exc.headers))
                    continue
                raise _HttpFailure(
                    _error_message(payload, f"客户端包下载失败：HTTP {exc.code}"),
                    payload=payload,
                    status=exc.code,
                ) from exc
            except (TimeoutError, URLError, OSError) as exc:
                if attempt < attempts:
                    _backoff(attempt)
                    continue
                raise _HttpFailure(f"客户端包下载失败：{exc}", payload=None) from exc
        raise AssertionError("unreachable")

    def update_client_package(
        self,
        *,
        package_dir: str | Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Fetch, verify, and replace the local portable client package."""
        manifest = self.call_tool("get_skill_manifest")
        if not isinstance(manifest, dict) or not manifest.get("download_url"):
            raise McpError("get_skill_manifest 返回格式不正确", payload=manifest)
        target = Path(package_dir).expanduser().resolve() if package_dir else Path(__file__).resolve().parents[2]
        local_version = ""
        version_file = target / "VERSION"
        if version_file.is_file():
            local_version = version_file.read_text(encoding="utf-8").strip()

        manifest_version = str(manifest.get("version") or "").strip()
        expected_sha = str(manifest.get("sha256") or "").strip().lower()
        if not expected_sha:
            raise McpError("清单缺少必需的 SHA-256 校验和", payload=manifest)

        def _parse_version(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(p) for p in re.findall(r"\d+", str(v)))
            except Exception:
                return ()

        if not force and local_version:
            if local_version == manifest_version:
                return {
                    "updated": False,
                    "reason": "already_current",
                    "version": local_version,
                    "sha256": expected_sha,
                    "directory": str(target),
                }
            if _parse_version(manifest_version) < _parse_version(local_version):
                return {
                    "updated": False,
                    "reason": "server_version_lower",
                    "version": local_version,
                    "server_version": manifest_version,
                    "sha256": expected_sha,
                    "directory": str(target),
                }

        package = self.download_client_package(self._upload_url(str(manifest["download_url"])))
        digest = hashlib.sha256(package).hexdigest()
        if digest != expected_sha:
            raise McpError("客户端包 SHA-256 校验失败", payload={"expected": expected_sha, "actual": digest})

        with tempfile.TemporaryDirectory(prefix="team-assets-client-update-") as staging_name:
            staging = Path(staging_name)
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                members = archive.namelist()
                archive.extractall(staging)
            extracted = staging / "team-assets-mcp-client"
            if not extracted.is_dir():
                raise McpError("客户端包缺少 team-assets-mcp-client 根目录")
            for member in members:
                member_path = (staging / member).resolve()
                if not member_path.is_relative_to(extracted.resolve()) and member_path != extracted.resolve():
                    raise McpError("客户端包包含越界路径")

            required_files = ["SKILL.md", "VERSION", "scripts/team_assets_mcp.py"]
            for rf in required_files:
                if not (extracted / rf).is_file():
                    raise McpError(f"客户端包缺少必要文件: {rf}")

            extracted_version = (extracted / "VERSION").read_text(encoding="utf-8").strip()
            if manifest_version and extracted_version != manifest_version:
                raise McpError(
                    "客户端包 VERSION 与清单版本不一致",
                    payload={"manifest": manifest_version, "package": extracted_version},
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            install_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.new-", dir=target.parent))
            backup_dir = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
            backup_moved = False
            try:
                shutil.copytree(extracted, install_dir, dirs_exist_ok=True)
                for rf in required_files:
                    if not (install_dir / rf).is_file():
                        raise McpError(f"暂存客户端包缺少必要文件: {rf}")

                if target.exists():
                    os.replace(target, backup_dir)
                    backup_moved = True
                try:
                    os.replace(install_dir, target)
                except Exception as install_error:
                    if backup_moved:
                        try:
                            os.replace(backup_dir, target)
                            backup_moved = False
                        except Exception as restore_error:
                            raise McpError(
                                "新包安装失败，且旧包自动恢复失败；旧包仍保留在备份目录",
                                payload={"backup_directory": str(backup_dir), "install_error": str(install_error)},
                            ) from restore_error
                    raise

                if backup_moved:
                    try:
                        shutil.rmtree(backup_dir)
                        backup_moved = False
                    except OSError:
                        # The new package is already installed; retain the backup for manual cleanup.
                        pass
            finally:
                if install_dir.exists():
                    shutil.rmtree(install_dir, ignore_errors=True)

        return {
            "updated": True,
            "version": manifest.get("version"),
            "sha256": digest,
            "directory": str(target),
            "reload_required": True,
        }
