"""Transport, HTTP JSON-RPC/SSE protocols, error handling, and tool execution."""
from __future__ import annotations

import json
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .config import (
    DEFAULT_ENDPOINT,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    RETRYABLE_HTTP_STATUS,
    SAFE_READ_METHODS,
    SAFE_READ_TOOLS,
)


class McpError(RuntimeError):
    """An MCP/JSON-RPC/tool failure with its structured payload preserved."""

    def __init__(self, message: str, *, payload: Any = None, status: int | None = None):
        super().__init__(message)
        self.payload = payload
        self.status = status


class _HttpFailure(McpError):
    pass


def _json_loads(data: bytes) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data.decode("utf-8", errors="replace")


def _decode_response(data: bytes, content_type: str) -> Any:
    """Decode JSON or the single-message SSE response used by MCP HTTP."""
    if "text/event-stream" not in content_type.lower():
        return _json_loads(data)
    messages: list[Any] = []
    current: list[str] = []
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if raw == "":
            if current:
                text = "\n".join(current)
                try:
                    messages.append(json.loads(text))
                except json.JSONDecodeError:
                    messages.append(text)
                current = []
            continue
        if raw.startswith("data:"):
            current.append(raw[5:].lstrip())
    if current:
        text = "\n".join(current)
        try:
            messages.append(json.loads(text))
        except json.JSONDecodeError:
            messages.append(text)
    return messages[-1] if messages else None


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("error"), dict):
            return str(payload["error"].get("message") or fallback)
        if payload.get("message"):
            return str(payload["message"])
        if isinstance(payload.get("detail"), dict) and payload["detail"].get("message"):
            return str(payload["detail"]["message"])
        if payload.get("detail") and isinstance(payload.get("detail"), str):
            return payload["detail"]
    return fallback


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers else None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(seconds, 30.0))


def _backoff(attempt: int, retry_after: float | None = None) -> None:
    if retry_after is not None:
        delay = retry_after
    else:
        # Small jitter prevents several AI clients retrying a shared NAS in lockstep.
        delay = min(8.0, 0.35 * (2**attempt)) + random.uniform(0, 0.15)
    time.sleep(delay)


class TransportMixin:
    """HTTP transport layer for Streamable MCP protocol."""

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self, *, upload_token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
            "User-Agent": "team-assets-mcp-client/1.0",
        }
        bearer = upload_token if upload_token is not None else self.token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry: bool = False,
    ) -> tuple[Any, Any]:
        attempts = self.retries if retry else 0
        for attempt in range(attempts + 1):
            request = Request(url, data=body, headers=headers or {}, method=method)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                    if response.headers.get("Mcp-Session-Id"):
                        self.session_id = response.headers["Mcp-Session-Id"]
                    return _decode_response(content, response.headers.get("Content-Type", "")), response.headers
            except HTTPError as exc:
                raw = exc.read()
                payload = _decode_response(raw, exc.headers.get("Content-Type", ""))
                if exc.code in RETRYABLE_HTTP_STATUS and attempt < attempts:
                    _backoff(attempt, _retry_after(exc.headers))
                    continue
                raise _HttpFailure(
                    _error_message(payload, f"MCP HTTP {exc.code}"), payload=payload, status=exc.code
                ) from exc
            except (TimeoutError, URLError, OSError) as exc:
                if attempt < attempts:
                    _backoff(attempt)
                    continue
                raise _HttpFailure(f"无法连接 MCP 服务：{exc}", payload=None) from exc
        raise AssertionError("unreachable")

    def initialize(self) -> dict[str, Any] | None:
        # The session ID and initialized flag belong to this client instance.
        # Keep the whole handshake atomic so concurrent first calls do not start
        # multiple server sessions and race on the active session header.
        with self._rpc_lock:
            if self.initialized:
                return None
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "team-assets-mcp-client", "version": "1.0"},
                },
            }
            result = self._rpc_payload(payload, retry=True)
            notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            self._request(
                "POST",
                self.endpoint,
                body=json.dumps(notification, ensure_ascii=False).encode("utf-8"),
                headers=self._headers(),
                retry=False,
            )
            self.initialized = True
            return result if isinstance(result, dict) else None

    def _rpc_payload(self, payload: dict[str, Any], *, retry: bool) -> Any:
        response, _headers = self._request(
            "POST",
            self.endpoint,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            retry=retry,
        )
        if response is None:
            return None
        if not isinstance(response, dict):
            raise McpError("MCP 返回了无法解析的响应", payload=response)
        if "error" in response:
            error = response["error"]
            raise McpError(_error_message(error, "JSON-RPC 请求失败"), payload=response)
        return response.get("result")

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry: bool | None = None,
        ensure_initialized: bool | None = None,
    ) -> Any:
        # A single synchronous MCP client shares request IDs, initialization
        # state, and one transport session. Serialize complete RPC exchanges so
        # IDs remain unique and every request uses the negotiated session.
        with self._rpc_lock:
            if ensure_initialized is None:
                ensure_initialized = self.auto_initialize and method not in {
                    "initialize",
                    "notifications/initialized",
                }
            if ensure_initialized and not self.initialized:
                self.initialize()
            payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
            if params is not None:
                payload["params"] = params
            if method == "notifications/initialized":
                payload.pop("id", None)
            if retry is None:
                retry = method in SAFE_READ_METHODS
            return self._rpc_payload(payload, retry=retry)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, retries: int | None = None) -> Any:
        args = arguments or {}
        retryable_write = isinstance(args.get("idempotency_key"), str) and bool(args["idempotency_key"].strip())
        transport_retry = retryable_write or name in SAFE_READ_TOOLS
        attempts = self.retries if retries is None else max(0, retries)
        for attempt in range(attempts + 1):
            try:
                result = self.rpc(
                    "tools/call",
                    {"name": name, "arguments": args},
                    retry=transport_retry,
                )
            except _HttpFailure as exc:
                if retryable_write and attempt < attempts and exc.status in RETRYABLE_HTTP_STATUS:
                    _backoff(attempt)
                    continue
                raise
            if isinstance(result, dict) and result.get("isError"):
                structured = result.get("structuredContent")
                error = structured.get("error") if isinstance(structured, dict) else None
                if isinstance(error, dict) and error.get("retryable") and retryable_write and attempt < attempts:
                    _backoff(attempt)
                    continue
                raise McpError(_error_message(structured or result, "MCP 工具调用失败"), payload=result)
            if not isinstance(result, dict):
                return result
            if "structuredContent" in result:
                return result["structuredContent"]
            for item in result.get("content", []):
                if item.get("type") == "text":
                    try:
                        return json.loads(item.get("text", ""))
                    except json.JSONDecodeError:
                        return item.get("text")
            return result
        raise AssertionError("unreachable")

    def put_chunk(self, upload_url: str, upload_token: str, offset: int, body: bytes) -> dict[str, Any]:
        parsed = urlparse(upload_url)
        query = parse_qs(parsed.query)
        query["offset"] = [str(offset)]
        target = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)),
            "Authorization": f"Bearer {upload_token}",
            "User-Agent": "team-assets-mcp-client/1.0",
        }
        response, _ = self._request("PUT", target, body=body, headers=headers, retry=False)
        if not isinstance(response, dict):
            raise McpError("上传块返回了无法解析的响应", payload=response)
        return response

    def _upload_url(self, value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value
        return urljoin(self.endpoint, value)
