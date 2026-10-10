"""TeamAssetsMCP client class aggregating transport, uploader, and updater mixins."""
from __future__ import annotations

import os
import threading

from .config import DEFAULT_ENDPOINT, DEFAULT_PROTOCOL_VERSION, DEFAULT_RETRIES, DEFAULT_TIMEOUT
from .transport import TransportMixin
from .uploader import UploaderMixin
from .updater import UpdaterMixin


class TeamAssetsMCP(TransportMixin, UploaderMixin, UpdaterMixin):
    """Minimal Streamable HTTP MCP client."""

    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        protocol_version: str | None = None,
        retries: int = DEFAULT_RETRIES,
        auto_initialize: bool = True,
    ) -> None:
        endpoint = endpoint or os.environ.get("TEAM_ASSETS_MCP_URL", DEFAULT_ENDPOINT)
        token = token if token is not None else os.environ.get("TEAM_ASSETS_MCP_TOKEN", "")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.protocol_version = protocol_version or os.environ.get(
            "TEAM_ASSETS_MCP_PROTOCOL_VERSION", DEFAULT_PROTOCOL_VERSION
        )
        self.retries = max(0, int(retries))
        self.auto_initialize = auto_initialize
        self.session_id: str | None = None
        self.initialized = False
        self._request_id = 0
        self._rpc_lock = threading.RLock()
