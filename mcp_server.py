"""Streamable HTTP MCP adapter for the shared asset service.

The NAS deployment defaults to LAN-only, no-bearer access.  Set
``MCP_AUTH_MODE=bearer`` and ``TEAM_ASSETS_MCP_TOKEN`` when an authenticated
deployment is wanted.  Upload chunks still use their short-lived per-session
token so an upload URL cannot be written by an unrelated request.

Official SDK compatibility is pinned to mcp==1.26.0.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import os
import json
import shutil
import re
import zipfile
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations, CallToolResult, TextContent
from pydantic import BaseModel, Field, ConfigDict
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from services.facade import AssetService
from uploads import UploadManager
from intake import IntakeService
from episodes import EPISODES_HELP, EPISODE_USAGE_HELP

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * ((MAX_UPLOAD_BYTES + 2) // 3) + 64 * 1024
CLIENT_PACKAGE_ROOT = Path(__file__).resolve().parent / "mcp-client"
try:
    CLIENT_PACKAGE_VERSION = (CLIENT_PACKAGE_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "2.5.0"
except OSError:
    CLIENT_PACKAGE_VERSION = "2.5.0"
CONFIRM = "用户已明确授权的操作直接执行，无需重复确认；目标或字段不明确时先询问。同名对象先消歧，再使用稳定 ID。"
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def _client_package_files() -> list[Path]:
    """Return only the portable client files, excluding caches and artifacts."""
    if not CLIENT_PACKAGE_ROOT.is_dir():
        return []
    return [
        path for path in sorted(CLIENT_PACKAGE_ROOT.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]


def _build_client_package() -> tuple[bytes, list[str]]:
    files = _client_package_files()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(CLIENT_PACKAGE_ROOT).as_posix()
            archive.write(path, f"team-assets-mcp-client/{relative}")
    return output.getvalue(), [path.relative_to(CLIENT_PACKAGE_ROOT).as_posix() for path in files]


class UploadImage(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    base64_data: str = Field(description="原始图片字节的标准 Base64，不含 data: 前缀。")


class IntakeAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    category: str = "人物"
    role: str = ""


class IntakeVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: str | None = Field(default=None, description="已有变体 ID；提供时只挂接文件，不修改文字。否则提供 name 创建变体。")
    name: str | None = None
    episodes: str = Field(default="", max_length=2000, description=EPISODES_HELP)
    description: str = ""
    episode_usages: list[dict[str, Any]] = Field(default_factory=list, description=EPISODE_USAGE_HELP)
    file_ids: list[str] = Field(default_factory=list, description="complete_upload 返回的已就绪 file_id。")
    material_names: dict[str, str] = Field(default_factory=dict, description="可选 file_id 到素材显示名称的映射。")


def decode_uploads(files: list[UploadImage]) -> list[tuple[str, bytes]]:
    if not 1 <= len(files) <= 20:
        raise ValueError("每次上传 1–20 张图片")
    if sum(len(item.base64_data) for item in files) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
        raise ValueError("一次上传图片总量不能超过 30 MB")
    uploads = []
    total = 0
    for item in files:
        try:
            content = base64.b64decode(item.base64_data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"{item.filename} 的 Base64 无效") from None
        if not content:
            raise ValueError("不能上传空文件")
        total += len(content)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("一次上传图片总量不能超过 30 MB")
        uploads.append((item.filename, content))
    return uploads


class BearerAndBodyLimitMiddleware:
    """Optionally authenticate and always cap MCP request bodies.

    In no-auth mode this middleware is deliberately still installed: it keeps
    the old request-size protection without requiring a server-wide secret.
    """

    def __init__(self, app, token: str | None = None, require_auth: bool = False):
        self.app = app
        self.token = token.encode("utf-8") if token else b""
        self.require_auth = require_auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if re.fullmatch(r"/uploads/[a-f0-9]{32}", scope.get("path", "")):
            return await self.app(scope, receive, send)
        if self.require_auth:
            headers = dict(scope.get("headers", []))
            value = headers.get(b"authorization", b"")
            parts = value.split(b" ", 1)
            if len(parts) != 2 or parts[0].lower() != b"bearer" or not hmac.compare_digest(parts[1], self.token):
                return await JSONResponse({"error": "MCP Bearer token required"}, status_code=401,
                                          headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await JSONResponse({"error": "Request exceeds upload limit"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(data_dir: str | Path | None = None, token: str | None = None,
               auth_mode: str | None = None, streamable_http_path: str = "/mcp",
               *, service=None, read_only: bool | None = None):
    """Create the MCP ASGI app.

    ``auth_mode`` accepts ``none`` (the default for the NAS) or ``bearer``.
    Passing a token directly keeps the test/developer convenience of enabling
    bearer mode without an environment variable.  An explicit environment
    value always wins for container deployments.
    """
    configured_mode = auth_mode if auth_mode is not None else os.environ.get("MCP_AUTH_MODE")
    if configured_mode is None:
        configured_mode = "bearer" if token is not None else "none"
    mode = configured_mode.strip().lower()
    if mode in {"off", "disabled", "no", "public", "unauthenticated"}:
        mode = "none"
    elif mode in {"token", "required", "authenticated"}:
        mode = "bearer"
    if mode not in {"none", "bearer"}:
        raise RuntimeError("MCP_AUTH_MODE must be none or bearer")
    secret = token if token is not None else os.environ.get("TEAM_ASSETS_MCP_TOKEN", "")
    if mode == "bearer" and (not secret or not secret.strip()):
        raise RuntimeError("Set TEAM_ASSETS_MCP_TOKEN before starting the MCP server")
    configured_read_only = (
        os.environ.get("TEAM_ASSETS_READ_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
        if read_only is None
        else bool(read_only)
    )
    if service is None:
        service = AssetService(
            data_dir or os.environ.get("DATA_DIR", str(Path(__file__).parent / "data")),
            read_only=configured_read_only,
        )
    elif read_only is not None and bool(getattr(service, "read_only", False)) != configured_read_only:
        raise ValueError("read_only must match the injected service mode")
    uploads = UploadManager(service)
    intake = IntakeService(service)
    mcp = FastMCP("Team Assets", host="0.0.0.0", port=8001, stateless_http=True,
                  streamable_http_path=streamable_http_path, json_response=True,
                  instructions="团队共享资产库。先读取并核对稳定 ID，同名时询问目标。" + CONFIRM +
                  "上传默认为候选素材。用户明确要求设定稿时即可执行。大文件使用上传会话，通过 HTTP 传输文件后批量入库；不接受服务器本地路径。")

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            code = detail.get("code") if isinstance(detail, dict) else ""
            msg = str(detail)
            if exc.status_code in (429, 503):
                retryable = True
            elif exc.status_code == 409:
                if code in ("IDEMPOTENCY_CONFLICT", "NAME_CONFLICT", "CANDIDATE_ALREADY_REFERENCED", "VERSION_CONFLICT", "upload_incomplete") or "Idempotency key reused" in msg:
                    retryable = False
                else:
                    retryable = detail.get("offset") is not None
            else:
                retryable = False
            error = {"code": {404: "not_found", 409: "conflict", 422: "validation_error", 413: "payload_too_large", 507: "insufficient_storage"}.get(exc.status_code, "request_failed"),
                     "message": detail.get("message", detail.get("detail", str(detail.get("code", "请求失败")))), "status": exc.status_code,
                     "retryable": retryable}
            error.update({k: v for k, v in detail.items() if k in ("code", "current", "version", "candidates", "offset", "size", "parent_id", "parent_type")})
            return CallToolResult(isError=True, structuredContent={"error": error}, content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))])
        except ValueError as exc:
            error = {"code": "validation_error", "message": str(exc), "status": 422, "retryable": False}
            return CallToolResult(isError=True, structuredContent={"error": error}, content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))])
        except OSError:
            error = {"code": "storage_error", "message": "存储操作失败，请检查服务端存储和日志", "status": 500, "retryable": False}
            return CallToolResult(isError=True, structuredContent={"error": error}, content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))])

    @mcp.tool(annotations=READ)
    def get_workspace() -> dict[str, Any]:
        """读取项目、资产、变体和素材；通过稳定 ID 定位目标，获取编辑所需版本号。"""
        return invoke(service.state)

    @mcp.tool(description=CONFIRM + "创建团队共享项目。", annotations=WRITE)
    def create_project(name: str, description: str = "") -> dict[str, Any]:
        return invoke(service.create_project, name=name, description=description)

    @mcp.tool(description=CONFIRM + "在指定项目创建资产。", annotations=WRITE)
    def create_asset(project_id: str, name: str, category: str = "人物", role: str = "") -> dict[str, Any]:
        return invoke(service.create_asset, project_id=project_id, name=name, category=category, role=role)

    @mcp.tool(description=CONFIRM + "修改资产名称、类型或定位/用途，使用最新 version 防止覆盖他人修改。", annotations=WRITE)
    def update_asset(asset_id: str, version: int, name: str | None = None,
                     category: str | None = None, role: str | None = None) -> dict[str, Any]:
        changes = {key: value for key, value in {"name": name, "category": category, "role": role}.items() if value is not None}
        return invoke(service.update_asset, asset_id, version=version, **changes)

    @mcp.tool(description=CONFIRM + "在指定资产创建造型或状态变体。" + EPISODES_HELP, annotations=WRITE)
    def create_variant(asset_id: str, name: str, episodes: str = "", description: str = "",
                       episode_usages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """创建状态变体。episodes 使用规范数字区间，说明文字放 description。"""
        return invoke(service.create_variant, asset_id=asset_id, name=name, episodes=episodes,
                      description=description, episode_usages=episode_usages or [])

    @mcp.tool(description=CONFIRM + "更新变体文字，使用最新 version 防止覆盖他人修改。" + EPISODES_HELP, annotations=WRITE)
    def update_variant(variant_id: str, version: int, name: str | None = None,
                       episodes: str | None = None, description: str | None = None,
                       episode_usages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        changes = {key: value for key, value in {"name": name, "episodes": episodes, "description": description,
                                                  "episode_usages": episode_usages}.items() if value is not None}
        return invoke(service.update_variant, variant_id, version=version, **changes)

    @mcp.tool(description=CONFIRM + "上传到资产共享候选池：asset_id 与 variant_id 恰选一个，变体仅记录上传来源。兼容小文件 Base64 上传：总计最多30MB、20张。大文件请使用 begin_upload 会话，无固定文件大小上限；上传完成后 commit_intake 入库。使用同一 idempotency_key 重试同一次上传，避免重复入库。不会自动设定稿。", annotations=WRITE)
    def upload_materials(files: list[UploadImage], idempotency_key: str,
                         variant_id: str | None = None, asset_id: str | None = None) -> dict[str, Any]:
        def upload():
            if not idempotency_key.strip() or len(idempotency_key) > 200:
                raise ValueError("idempotency_key 应为 1–200 字符的唯一请求标识")
            return service.add_materials(variant_id, decode_uploads(files), source="ai", idempotency_key=idempotency_key, asset_id=asset_id)
        return invoke(upload)

    @mcp.tool(description=CONFIRM + "material_id 必须属于变体所在的同一资产，且未删除。同一候选素材可被该资产多个变体分别引用为定稿；修改一个变体不会改变其他变体。version 从最新数据读取。", annotations=WRITE)
    def set_final_material(variant_id: str, material_id: str, version: int) -> dict[str, Any]:
        return invoke(service.update_variant, variant_id, version=version, final_material_id=material_id)

    @mcp.tool(description=CONFIRM + "将资产候选素材移入回收站，并同时清除所有变体对该素材的定稿引用。素材可恢复，但不会自动重新设为定稿。", annotations=WRITE)
    def delete_material(material_id: str) -> dict[str, Any]:
        return invoke(service.delete_material, material_id)

    @mcp.tool(description=CONFIRM + "恢复回收站中的素材。", annotations=WRITE)
    def restore_material(material_id: str) -> dict[str, Any]:
        return invoke(service.restore_material, material_id)

    @mcp.tool(annotations=READ)
    def list_trash() -> Any:
        """查看回收站中的项目、资产、变体和素材；恢复子项前先恢复已删除父项。"""
        return invoke(service.trash)

    @mcp.tool(annotations=READ)
    def list_history() -> Any:
        """查看最近资产变更记录。"""
        return invoke(service.history)

    @mcp.tool(annotations=READ)
    def list_projects(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """分页列出项目及稳定 ID。"""
        return invoke(service.list_projects, limit=limit, offset=offset)

    @mcp.tool(annotations=READ)
    def search_assets(query: str = "", project_id: str | None = None, category: str | None = None,
                      limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """按名称、项目和分类搜索资产。同名时根据项目路径确认目标，不默认选择第一项。"""
        return invoke(service.search_assets, query=query, project_id=project_id, category=category, limit=limit, offset=offset)

    @mcp.tool(annotations=READ)
    def get_asset(asset_id: str) -> dict[str, Any]:
        """读取指定资产及其变体，获取最新版本和目标关系。"""
        return invoke(service.get_asset, asset_id)

    @mcp.tool(annotations=READ)
    def get_variant(variant_id: str) -> dict[str, Any]:
        """读取指定变体详情与路径。"""
        return invoke(service.get_variant, variant_id)

    @mcp.tool(annotations=READ)
    def audit_project_asset_coverage(project_id: str, include_disabled: bool = False) -> dict[str, Any]:
        """生成项目素材覆盖报告：逐资产/变体统计可用素材、定稿和缺口。

        资产 coverage_status 表示是否有可用素材；candidate_coverage_status 单独表示是否有候选引用。
        默认按前端正常视图排除禁用候选；include_disabled=true 可用于后端审计，
        以区分“没有素材”和“素材存在但被禁用”。
        """
        return invoke(service.audit_project_asset_coverage, project_id, include_disabled)

    @mcp.tool(annotations=READ)
    def plan_project_import(project_id: str, assets: list[dict[str, Any]],
                            on_name_conflict: Literal["error", "reuse"] = "error") -> dict[str, Any]:
        """校验 Markdown/JSON 导入计划但不写入。先处理 errors，再调用 apply_project_import。"""
        return invoke(intake.plan_project_import, project_id, assets, on_name_conflict=on_name_conflict)

    @mcp.tool(description=CONFIRM + "按已校验的项目清单原子创建或复用资产、变体、素材和候选引用；同一 idempotency_key 可安全重试。", annotations=WRITE)
    def apply_project_import(project_id: str, assets: list[dict[str, Any]], idempotency_key: str,
                             on_name_conflict: Literal["error", "reuse"] = "error") -> dict[str, Any]:
        """写入经过 plan_project_import 校验的多资产清单。"""
        return invoke(intake.apply_project_import, project_id, assets, idempotency_key,
                      on_name_conflict=on_name_conflict)

    @mcp.tool(annotations=READ)
    def list_materials(variant_id: str | None = None, limit: int = 50, offset: int = 0,
                       asset_id: str | None = None) -> dict[str, Any]:
        """分页列出资产共享候选素材；asset_id 与 variant_id 恰选一个。传变体时列出其资产的全部候选。variant_id 仅记录上传来源，不限制定稿引用。"""
        return invoke(service.list_materials, variant_id, limit=limit, offset=offset, asset_id=asset_id)

    @mcp.tool(annotations=READ)
    def list_candidates(project_id: str, query: str = "", category: str | None = None,
                        status: Literal["active", "disabled", "all"] = "active",
                        limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """分页列出项目候选库。query 只匹配文件名；status=disabled 对应“仅显示禁用”。"""
        return invoke(service.list_candidates, project_id, query=query, category=category,
                      status=status, limit=limit, offset=offset)

    @mcp.tool(annotations=READ)
    def list_candidate_folders(project_id: str,
                               status: Literal["active", "disabled", "all"] = "all") -> dict[str, Any]:
        """读取候选库由上传路径自动生成的目录树；目录状态会继承到子目录和文件。"""
        return invoke(service.list_candidate_folders, project_id, status=status)

    @mcp.tool(annotations=READ)
    def get_candidate(candidate_id: str) -> dict[str, Any]:
        """读取候选素材的文件、缩略图、禁用状态和资产引用。"""
        return invoke(service.get_candidate, candidate_id)

    @mcp.tool(description=CONFIRM + "将已 complete_upload 的文件登记为项目候选素材。目标目录请使用 folder_path；relative_path 仅兼容旧版完整相对路径。每次登记都是新候选记录，允许相同文件重复；原图文件按 SHA-256 复用。", annotations=WRITE)
    def register_candidate(project_id: str, file_id: str, filename: str,
                           category: str = "其它", relative_path: str | None = None,
                           folder_path: str | None = None) -> dict[str, Any]:
        """大图或视频流程：begin_upload → 传输 → complete_upload → register_candidate。"""
        return invoke(service.create_candidate_from_file, project_id, file_id, filename,
                      category=category, relative_path=relative_path, folder_path=folder_path)

    @mcp.tool(description=CONFIRM + "把候选素材以引用方式挂接到同项目资产；不会复制原文件。同一候选可引用到多个资产或变体。可附 match_status、confidence、note 记录 AI 匹配依据。", annotations=WRITE)
    def create_candidate_reference(candidate_id: str, asset_id: str,
                                   variant_id: str | None = None,
                                   match_status: Literal["unreviewed", "folder", "web", "exact", "approximate", "partial", "missing"] | None = None,
                                   confidence: float | None = None, note: str | None = None,
                                   source: str | None = None) -> dict[str, Any]:
        return invoke(service.create_candidate_reference, candidate_id, asset_id, variant_id,
                      match_status=match_status, confidence=confidence, note=note, source=source)

    @mcp.tool(description=CONFIRM + "修改候选素材文件名、分类或 active/disabled 状态，使用最新 version。", annotations=WRITE)
    def update_candidate(candidate_id: str, version: int, name: str | None = None,
                         category: str | None = None, status: Literal["active", "disabled"] | None = None) -> dict[str, Any]:
        changes = {key: value for key, value in {"name": name, "category": category, "status": status}.items() if value is not None}
        return invoke(service.update_candidate, candidate_id, version=version, **changes)

    @mcp.tool(description=CONFIRM + "重命名或禁用候选库文件夹；重命名会同步后代路径，禁用会继承到目录内文件。", annotations=WRITE)
    def update_candidate_folder(folder_id: str, version: int, name: str | None = None,
                                status: Literal["active", "disabled"] | None = None) -> dict[str, Any]:
        changes = {key: value for key, value in {"name": name, "status": status}.items() if value is not None}
        return invoke(service.update_candidate_folder, folder_id, version=version, **changes)

    @mcp.tool(description=CONFIRM + "把候选文件移入已有目录或根目录；目录不会由此接口新建。", annotations=WRITE)
    def move_candidate(candidate_id: str, version: int, target_folder_path: str = "") -> dict[str, Any]:
        return invoke(service.move_candidate, candidate_id, version, target_folder_path)

    @mcp.tool(description=CONFIRM + "提交同一父目录下文件夹与候选文件的完整混合顺序。", annotations=WRITE)
    def reorder_candidate_entries(project_id: str, parent_path: str,
                                  entries: list[dict[str, str]]) -> dict[str, Any]:
        return invoke(service.reorder_candidate_entries, project_id, parent_path, entries)

    @mcp.tool(description=CONFIRM + "让候选素材周围临时高亮 60 秒，用于团队提醒。", annotations=WRITE)
    def highlight_candidate(candidate_id: str) -> dict[str, Any]:
        return invoke(service.highlight_candidate, candidate_id, seconds=60)

    @mcp.tool(description=CONFIRM + "修改项目名称或说明，需要最新版本。", annotations=WRITE)
    def update_project(project_id: str, version: int, name: str | None = None, description: str | None = None) -> dict[str, Any]:
        return invoke(service.update_project, project_id, version, **{k:v for k,v in {"name":name,"description":description}.items() if v is not None})

    @mcp.tool(annotations=READ)
    def preview_deletion(entity_type: Literal["project", "asset", "variant"], item_id: str, version: int) -> dict[str, Any]:
        """只预览删除影响范围，不进行修改。"""
        return invoke(service.delete_entity, entity_type, item_id, version, dry_run=True)

    @mcp.tool(description=CONFIRM + "将项目及活跃子项移入回收站。可先 preview_deletion 查看影响范围。", annotations=WRITE)
    def delete_project(project_id: str, version: int) -> dict[str, Any]:
        return invoke(service.delete_entity, "project", project_id, version)

    @mcp.tool(description=CONFIRM + "将资产及活跃子项移入回收站。", annotations=WRITE)
    def delete_asset(asset_id: str, version: int) -> dict[str, Any]:
        return invoke(service.delete_entity, "asset", asset_id, version)

    @mcp.tool(description=CONFIRM + "将变体移入回收站；同资产共享候选素材和其他变体的定稿不受影响。", annotations=WRITE)
    def delete_variant(variant_id: str, version: int) -> dict[str, Any]:
        return invoke(service.delete_entity, "variant", variant_id, version)

    @mcp.tool(description=CONFIRM + "恢复该次项目删除影响的内容；此前单独删除的内容保持删除。", annotations=WRITE)
    def restore_project(project_id: str) -> dict[str, Any]:
        return invoke(service.restore_entity, "project", project_id)

    @mcp.tool(description=CONFIRM + "恢复资产及同批删除子项；父项目须先恢复。", annotations=WRITE)
    def restore_asset(asset_id: str) -> dict[str, Any]:
        return invoke(service.restore_entity, "asset", asset_id)

    @mcp.tool(description=CONFIRM + "恢复变体；父资产须先恢复。旧版本同批删除的素材也会恢复。", annotations=WRITE)
    def restore_variant(variant_id: str) -> dict[str, Any]:
        return invoke(service.restore_entity, "variant", variant_id)

    @mcp.tool(description=CONFIRM + "取消当前定稿，保留所有候选素材。", annotations=WRITE)
    def clear_final_material(variant_id: str, version: int) -> dict[str, Any]:
        return invoke(service.update_variant, variant_id, version=version, final_material_id=None)

    @mcp.tool(annotations=READ)
    def get_material_preview(material_id: str) -> Any:
        """返回素材缩略图供视觉模型查看；不下载原图，不接受文件路径。"""
        def preview():
            with service.connect() as c:
                material = service.require(c, "materials", material_id)
                service.require(c, "assets", material["asset_id"])
                file = service.require(c, "files", material["file_id"])
            path = (service.root / file["thumbnail"]).resolve()
            if not path.is_relative_to((service.root / "thumbnails").resolve()):
                raise HTTPException(422, "无效缩略图")
            if path.stat().st_size > 5 * 1024 * 1024:
                raise HTTPException(413, "缩略图过大")
            return Image(data=path.read_bytes(), format="jpeg")
        return invoke(preview)

    def client_manifest() -> dict[str, Any]:
        package, files = _build_client_package()
        origin = os.environ.get("TEAM_ASSETS_PUBLIC_MCP_URL", "").rstrip("/")
        download_path = "/mcp-client/download"
        return {
            "package": "team-assets-mcp-client",
            "version": CLIENT_PACKAGE_VERSION,
            "sha256": hashlib.sha256(package).hexdigest(),
            "size": len(package),
            "files": files,
            "skill_file": "SKILL.md",
            "download_url": origin + download_path if origin else download_path,
            "download_path": download_path,
            "reload_required": True,
            "update_scope": "portable client and Skill files; does not modify a hosted model's system prompt",
        }

    @mcp.tool(annotations=READ)
    def get_skill_manifest() -> dict[str, Any]:
        """查询 NAS 上最新版 Team Assets Skill/客户端包的版本、SHA-256 和下载地址。

        有本地文件权限的客户端可下载并校验 zip 后替换自己的 mcp-client 目录；
        托管式 AI 只能读取结果并在当前会话重新加载 Skill。
        """
        return client_manifest()

    @mcp.tool(annotations=READ)
    def get_capabilities() -> dict[str, Any]:
        """读取版本、上传协议、大小限制和存储余量。文件原图不压缩；HTTP 客户端负责传输字节。"""
        return {"version": "2.5", "authentication": {"mode": mode, "mcp_bearer": mode == "bearer",
                                                          "upload_session_token": True},
                "uploads": uploads.capabilities(),
                "episodes": {"input": "canonical_string", "syntax": EPISODES_HELP,
                             "episode_usages": EPISODE_USAGE_HELP,
                             "output_field": "episode_scope", "kinds": ["all", "unknown", "ranges"],
                             "range_shape": {"start": "positive integer", "end": "inclusive integer or null (unbounded)"},
                             "legacy_review_field": "episodes_needs_review"},
                "legacy_base64": {"max_bytes": MAX_UPLOAD_BYTES, "max_files": 20},
                "intake_max_variants": 100, "free_disk_bytes": shutil.disk_usage(service.root).free,
                 "candidate_library": {"statuses": ["active", "disabled"], "search": "filename_only", "highlight_seconds": 60,
                                       "folders": True, "mixed_order": True, "inherited_folder_disable": True,
                                       "duplicate_records": True, "physical_file_deduplication": True,
                                       "reference_flow": "complete_upload_then_register_candidate",
                                       "reference_metadata": {"match_status": ["unreviewed", "folder", "web", "exact", "approximate", "partial", "missing"], "confidence": "0..1", "note": "string", "source": "string"}},
                 "project_import": {"plan": True, "apply": True, "atomic": True, "max_assets": 200,
                                    "candidate_field": "candidate_ids", "candidate_metadata_field": "candidate_metadata", "appearance_field": "episode_usages"},
                 "coverage_audit": {"tool": "audit_project_asset_coverage", "disabled_policy": "include_disabled"},
                 "skill_updates": {"tool": "get_skill_manifest", "version": CLIENT_PACKAGE_VERSION,
                                   "download_path": "/mcp-client/download", "sha256": "returned_by_tool"},
                 "features": ["cascade_trash", "restore", "search", "preview", "clear_final", "atomic_intake", "resumable_upload", "structured_episodes", "episode_usages", "candidate_library", "candidate_references", "project_import", "coverage_audit", "skill_updates"]}

    @mcp.tool(description=CONFIRM + "申请大文件上传会话。文件大小无固定上限；用返回的专属 token 向 upload_url PUT 二进制块，URL 查询参数 ?offset=已接受字节数，Content-Type: application/octet-stream。每块不超过 chunk_size。网络失败后查询 offset 再继续。", annotations=WRITE)
    def begin_upload(filename: str, size: int, sha256: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        def begin():
            result = uploads.begin_upload(filename, size, sha256=sha256, idempotency_key=idempotency_key)
            origin = os.environ.get("TEAM_ASSETS_PUBLIC_MCP_URL", "").rstrip("/")
            result["upload_url"] = origin + result["relative_path"]
            result["url_is_absolute"] = bool(origin)
            return result
        return invoke(begin)

    @mcp.tool(annotations=READ)
    def get_upload_status(upload_id: str) -> dict[str, Any]:
        """查询已接受字节 offset；重试前检查进度。"""
        return invoke(uploads.status, upload_id)

    @mcp.tool(description=CONFIRM + "校验完整上传并生成缩略图，返回 file_id；尚未挂接资产，接着调用 commit_intake。", annotations=WRITE)
    def complete_upload(upload_id: str) -> dict[str, Any]:
        return invoke(uploads.complete_upload, upload_id)

    @mcp.tool(description=CONFIRM + "取消尚未完成的上传会话。", annotations=WRITE)
    def cancel_upload(upload_id: str) -> dict[str, Any]:
        return invoke(uploads.cancel_upload, upload_id)

    @mcp.tool(description=CONFIRM + "原子入库：asset_id 与 asset 恰选一个；每个变体选择已有 variant_id 或新建 name，file_ids 来自 complete_upload。一次最多100个变体，全部成功或全部回滚；同一 idempotency_key 重试同批操作。不会自动设定稿。", annotations=WRITE)
    def commit_intake(project_id: str, variants: list[IntakeVariant], idempotency_key: str,
                      asset_id: str | None = None, asset: IntakeAsset | None = None) -> dict[str, Any]:
        return invoke(intake.commit_intake, project_id=project_id, asset_id=asset_id,
                      asset=asset.model_dump(exclude_unset=True) if asset else None,
                      variants=[v.model_dump(exclude_unset=True) for v in variants], idempotency_key=idempotency_key)

    app = mcp.streamable_http_app()

    def download_client_package(request):
        package, _ = _build_client_package()
        return Response(package, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="team-assets-mcp-client-v{CLIENT_PACKAGE_VERSION}.zip"',
            "X-Team-Assets-Client-Version": CLIENT_PACKAGE_VERSION,
            "X-Team-Assets-Client-SHA256": hashlib.sha256(package).hexdigest(),
            "Cache-Control": "no-store",
        })

    app.router.routes.append(Route("/mcp-client/download", download_client_package, methods=["GET"]))
    app.router.routes.extend(uploads.routes)
    app.state.uploads = uploads
    app.state.service = service
    app.state.mcp = mcp
    app.add_middleware(BearerAndBodyLimitMiddleware, token=secret if mode == "bearer" else None,
                       require_auth=mode == "bearer")
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8001)
