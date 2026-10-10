import asyncio
import json
import pytest
from server import AssetService, create_app

@pytest.mark.anyio
async def test_listener_triggered_on_write_transaction(tmp_path):
    service = AssetService(tmp_path)
    event = service.register_change_listener()
    assert not event.is_set()

    # DB read should NOT trigger listener
    with service.connect(write=False) as c:
        c.execute("SELECT 1").fetchall()
    assert not event.is_set()

    # DB write should trigger listener
    proj = service.create_project("Project SSE")
    assert event.is_set()

    event.clear()
    assert not event.is_set()

    # Another write (e.g. create_asset)
    asset = service.create_asset(proj["id"], "Asset SSE")
    assert event.is_set()

    service.unregister_change_listener(event)
    event.clear()
    service.create_variant(asset["id"], "Variant SSE")
    # Unregistered event is not triggered
    assert not event.is_set()

@pytest.mark.anyio
async def test_sse_endpoint_push(tmp_path):
    app = create_app(tmp_path)
    service = app.state.service

    # Directly test the generator function inside /api/events endpoint
    route = None
    for r in app.routes:
        if getattr(r, "path", None) == "/api/events":
            route = r
            break
    assert route is not None

    class MockRequest:
        def __init__(self):
            self.disconnected = False
        async def is_disconnected(self):
            return self.disconnected

    req = MockRequest()
    response = await route.endpoint(req)
    gen = response.body_iterator

    # 1. Initial message yields current revision
    first_chunk = await anext(gen)
    assert first_chunk.startswith("data: ")
    data = json.loads(first_chunk.replace("data: ", "").strip())
    assert "revision" in data
    init_rev = data["revision"]

    # 2. Start waiting for next chunk in background task
    next_chunk_task = asyncio.create_task(anext(gen))
    await asyncio.sleep(0.05)
    # Ensure it is currently waiting/suspended (not finished)
    assert not next_chunk_task.done()

    # 3. Trigger a DB change on the app's service
    service.create_project("SSE Test Proj")

    # 4. next_chunk_task should wake up quickly and receive new revision
    chunk = await asyncio.wait_for(next_chunk_task, timeout=2.0)
    assert chunk.startswith("data: ")
    new_data = json.loads(chunk.replace("data: ", "").strip())
    assert new_data["revision"] > init_rev

    # Disconnect
    req.disconnected = True
