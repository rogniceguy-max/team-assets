"""Command-line interface parser and runner for Team Assets MCP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from .client import TeamAssetsMCP
from .config import DEFAULT_ENDPOINT, DEFAULT_RETRIES, DEFAULT_TIMEOUT
from .transport import McpError
from .uploader import _upload_command


def _json_argument(value: str | None, path: str | None) -> dict[str, Any]:
    if value and path:
        raise ValueError("--args 与 --args-file 只能提供一个")
    if path:
        value = Path(path).read_text(encoding="utf-8")
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("工具参数必须是 JSON object")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Team Assets MCP Streamable HTTP client")
    parser.add_argument(
        "--url",
        default=None,
        help=f"MCP URL；默认读取 TEAM_ASSETS_MCP_URL，未设置时使用 {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="可选 Bearer token；默认读取 TEAM_ASSETS_MCP_TOKEN（NAS 无认证模式无需填写）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities", help="读取服务器能力、上传限制和版本")
    sub.add_parser("tools", help="列出 MCP 工具")
    update = sub.add_parser("update", help="查询并更新本地 Team Assets Skill/客户端包")
    update.add_argument("--directory", help="要替换的 mcp-client 目录，默认是当前脚本所在目录")
    update.add_argument("--force", action="store_true", help="即使 VERSION 相同也重新下载并校验")
    call = sub.add_parser("call", help="调用任意 MCP 工具")
    call.add_argument("tool")
    call.add_argument("--args", help="JSON object")
    call.add_argument("--args-file", help="包含 JSON object 的文件")

    upload = sub.add_parser("upload", help="分块上传一个本地文件并登记为候选或资产素材")
    upload.add_argument("file")
    upload.add_argument("--mode", choices=("candidate", "asset"), default="candidate")
    upload.add_argument("--project-id", required=False)
    upload.add_argument("--idempotency-key")
    upload.add_argument("--name", help="候选/素材显示名称，默认使用本地文件名")
    upload.add_argument("--category", default="其它", choices=("人物", "场景", "道具", "其它"))
    upload.add_argument(
        "--folder-path", "--relative-path", dest="folder_path",
        help="候选库中的目标目录；--relative-path 是旧别名，参数值始终按目录解释",
    )
    upload.add_argument(
        "--file-relative-path",
        help="候选库中的完整文件相对路径；末级必须与最终文件名相同",
    )
    upload.add_argument("--asset-id")
    upload.add_argument("--asset-name")
    upload.add_argument("--role", default="")
    upload.add_argument("--variant-id")
    upload.add_argument("--variant-name")
    upload.add_argument("--episodes", default="")
    upload.add_argument("--description", default="")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        client = TeamAssetsMCP(args.url, args.token, timeout=args.timeout, retries=args.retries)
        if args.command == "capabilities":
            output = client.call_tool("get_capabilities")
        elif args.command == "tools":
            output = client.rpc("tools/list")
        elif args.command == "update":
            output = client.update_client_package(package_dir=args.directory, force=args.force)
        elif args.command == "call":
            output = client.call_tool(args.tool, _json_argument(args.args, args.args_file))
        else:
            if args.mode == "candidate" and not args.project_id:
                parser.error("candidate 模式需要 --project-id")
            output = _upload_command(client, args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (McpError, ValueError, OSError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "payload", None)
        message = {"error": str(exc)}
        if detail is not None:
            message["detail"] = detail
        print(json.dumps(message, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
