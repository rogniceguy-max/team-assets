import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-client" / "scripts"))
from mcp_client import TeamAssetsMCP


def test_concurrent_rpc_calls_allocate_unique_request_ids():
    client = TeamAssetsMCP("http://mcp.test", auto_initialize=False)
    request_ids = []

    def delayed_next_id():
        current = client._request_id
        time.sleep(0.005)
        client._request_id = current + 1
        return client._request_id

    def fake_request(method, url, *, body=None, headers=None, retry=False):
        payload = json.loads(body.decode("utf-8"))
        request_ids.append(payload["id"])
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}, {}

    client._next_id = delayed_next_id
    client._request = fake_request
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(
            lambda _: client.rpc("tools/list", retry=False, ensure_initialized=False), range(12)
        ))

    assert all(result == {"ok": True} for result in results)
    assert sorted(request_ids) == list(range(1, 13))


def test_concurrent_first_calls_initialize_one_session():
    client = TeamAssetsMCP("http://mcp.test", auto_initialize=True)
    counts_lock = threading.Lock()
    initialize_calls = 0
    tool_session_ids = []

    def fake_request(method, url, *, body=None, headers=None, retry=False):
        nonlocal initialize_calls
        payload = json.loads(body.decode("utf-8"))
        request_method = payload.get("method")
        if request_method == "initialize":
            with counts_lock:
                initialize_calls += 1
            time.sleep(0.02)
            headers = {"Mcp-Session-Id": "session-1"}
            client.session_id = headers["Mcp-Session-Id"]
            return {"jsonrpc": "2.0", "id": payload["id"], "result": {}}, headers
        if request_method == "notifications/initialized":
            return None, {}
        tool_session_ids.append((headers or {}).get("Mcp-Session-Id"))
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}, {}

    client._request = fake_request
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.rpc("tools/list", retry=False), range(8)))

    assert initialize_calls == 1
    assert results == [{"tools": []}] * 8
    assert tool_session_ids == ["session-1"] * 8
