import asyncio
import json
from types import SimpleNamespace

from server import AssetService, create_app


class ConnectedRequest:
    def __init__(self, app):
        self.app = app
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def test_sse_observes_revision_from_separate_service_instance(tmp_path):
    async def exercise():
        app = create_app(data_dir=tmp_path)
        reader = app.state.service
        writer = AssetService(data_dir=tmp_path)
        assert reader is not writer
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/events")
        request = ConnectedRequest(app)
        response = await endpoint(request)
        stream = response.body_iterator

        initial_chunk = await anext(stream)
        initial = initial_chunk.decode() if isinstance(initial_chunk, bytes) else initial_chunk
        assert "revision" in initial

        # A second service instance shares SQLite but has a different in-memory
        # event bus, matching the web and MCP process boundary.
        writer.create_project("Cross-process event probe")
        update_chunk = await asyncio.wait_for(anext(stream), timeout=2.5)
        update = update_chunk.decode() if isinstance(update_chunk, bytes) else update_chunk
        payload = json.loads(update.split("data: ", 1)[1].strip())
        assert payload["revision"] == writer.revision()
        assert payload["revision"] == reader.revision()

        request.disconnected = True
        try:
            await anext(stream)
        except StopAsyncIteration:
            pass

    asyncio.run(exercise())


def test_sse_shared_revision_polling_does_not_scale_with_listeners(tmp_path):
    async def exercise():
        app = create_app(data_dir=tmp_path)
        reader = app.state.service
        writer = AssetService(data_dir=tmp_path)
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/events")
        query_count = 0
        read_revision = reader.revision

        def counted_revision():
            nonlocal query_count
            query_count += 1
            return read_revision()

        reader.revision = counted_revision
        streams = []
        for _ in range(12):
            request = ConnectedRequest(app)
            response = await endpoint(request)
            stream = response.body_iterator
            initial = await anext(stream)
            assert "revision" in str(initial)
            streams.append((request, stream))

        before_write_queries = query_count
        writer.create_project("Shared SSE poll")
        updates = await asyncio.wait_for(
            asyncio.gather(*(anext(stream) for _, stream in streams)), timeout=3.0
        )
        assert query_count - before_write_queries <= 2
        expected_revision = writer.revision()
        for chunk in updates:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            payload = json.loads(text.split("data: ", 1)[1].strip())
            assert payload["revision"] == expected_revision

        for request, stream in streams:
            request.disconnected = True
            await stream.aclose()

    asyncio.run(exercise())
