#!/usr/bin/env python3
"""Small, dependency-free client for the Team Assets Streamable HTTP MCP server.

The NAS deployment is LAN-only and no-auth by default, so this package can be
copied to another AI client and used immediately. A custom endpoint or an
optional bearer token can still be supplied through options or environment
variables for a different deployment.

Examples:
  python scripts/team_assets_mcp.py capabilities
  python scripts/team_assets_mcp.py tools
  python scripts/team_assets_mcp.py call list_projects --args '{"limit": 10}'
  python scripts/team_assets_mcp.py upload ./render-4k.png --mode asset \
      --project-id PROJECT --asset-id ASSET --variant-id VARIANT
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure mcp_client package can be resolved when executed directly or imported
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from mcp_client.config import (
    DEFAULT_ENDPOINT,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    RETRYABLE_HTTP_STATUS,
    SAFE_READ_METHODS,
    SAFE_READ_TOOLS,
)
from mcp_client.transport import (
    McpError,
    _HttpFailure,
    _backoff,
    _decode_response,
    _error_message,
    _json_loads,
    _retry_after,
)
from mcp_client.uploader import (
    _progress,
    _sha256_file,
    _upload_command,
)
from mcp_client.updater import UpdaterMixin
from mcp_client.client import TeamAssetsMCP
from mcp_client.cli import (
    _build_parser,
    _json_argument,
    main,
)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "McpError",
    "RETRYABLE_HTTP_STATUS",
    "SAFE_READ_METHODS",
    "SAFE_READ_TOOLS",
    "TeamAssetsMCP",
    "UpdaterMixin",
    "main",
    "_HttpFailure",
    "_backoff",
    "_build_parser",
    "_decode_response",
    "_error_message",
    "_json_argument",
    "_json_loads",
    "_progress",
    "_retry_after",
    "_sha256_file",
    "_upload_command",
]

if __name__ == "__main__":
    raise SystemExit(main())
