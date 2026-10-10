"""Shared asset API. Database and immutable image files live under DATA_DIR."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.bus import EventBusMixin
from core.config import (
    CANDIDATE_CATEGORIES,
    CANDIDATE_MATCH_STATUSES,
    CANDIDATE_STATUSES,
    CHILDREN,
    FORMATS,
    MAX_UPLOAD,
    PARENTS,
    TABLES,
)
from core.db import DBMixin
from core.utils import now, uid
from media_utils import clean_relative_path, inspect_media
from routers.assets import router as assets_router
from routers.candidates import router as candidates_router
from routers.events import router as events_router
from routers.projects import router as projects_router
from schemas.models import (
    AssetInput,
    AssetPatch,
    CandidateFileInput,
    CandidateFolderPatch,
    CandidateMoveInput,
    CandidatePatch,
    CandidateReferenceInput,
    CandidateReorderEntry,
    CandidateReorderInput,
    ProjectInput,
    VariantInput,
    VariantPatch,
)
from services.asset_service import AssetMixin
from services.base import BaseDomainService
from services.candidate_service import CandidateMixin
from services.material_service import MaterialMixin
from services.project_service import ProjectMixin


class AssetService(
    DBMixin,
    EventBusMixin,
    BaseDomainService,
    ProjectMixin,
    AssetMixin,
    MaterialMixin,
    CandidateMixin,
):
    """Unified service facade aggregating domain services and infrastructure."""

    def __init__(self, data_dir):
        super().__init__(data_dir=data_dir)


def create_app(data_dir=None, static_dir=None, mcp_app=None):
    app = FastAPI(title="Team Assets", version="0.1.0")
    service = AssetService(data_dir or os.environ.get("DATA_DIR", str(Path(__file__).parent / "data")))
    app.state.service = service
    revision_poller_task = None
    revision_poller_ready = None

    async def poll_shared_revision(svc, ready):
        try:
            revision = await asyncio.to_thread(svc.revision)
            svc.publish_revision(revision, notify=False)
            ready.set()
            while True:
                await asyncio.sleep(1.0)
                revision = await asyncio.to_thread(svc.revision)
                svc.publish_revision(revision)
        finally:
            ready.set()

    async def ensure_revision_poller(svc):
        nonlocal revision_poller_task, revision_poller_ready
        if revision_poller_task is None or revision_poller_task.done():
            revision_poller_ready = asyncio.Event()
            revision_poller_task = asyncio.create_task(
                poll_shared_revision(svc, revision_poller_ready)
            )
        await revision_poller_ready.wait()

    @app.middleware("http")
    async def same_origin_writes(request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            try:
                allowed = urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "不允许跨站修改"}, status_code=403)
        return await call_next(request)

    app.include_router(projects_router)
    app.include_router(assets_router)
    app.include_router(candidates_router)
    app.include_router(events_router)

    @app.get("/api/events")
    async def events(request: Request):
        svc = (
            getattr(request, "app", None)
            and getattr(request.app, "state", None)
            and getattr(request.app.state, "service", None)
        ) or service

        async def stream():
            event = svc.register_change_listener()
            try:
                await ensure_revision_poller(svc)
                previous = svc.cached_revision()
                if previous is None:
                    previous = await asyncio.to_thread(svc.revision)
                    svc.publish_revision(previous, notify=False)
                yield f"data: {json.dumps({'revision': previous})}\n\n"
                last_keepalive = asyncio.get_running_loop().time()
                while not await request.is_disconnected():
                    try:
                        await asyncio.wait_for(event.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        pass
                    event.clear()
                    # One process-wide poll detects writes made by the other
                    # web/MCP process and broadcasts the cached revision to all
                    # listeners, instead of each SSE client querying SQLite.
                    revision = svc.cached_revision()
                    if revision is not None and revision != previous:
                        yield f"data: {json.dumps({'revision': revision})}\n\n"
                        previous = revision
                        last_keepalive = asyncio.get_running_loop().time()
                    elif asyncio.get_running_loop().time() - last_keepalive >= 15.0:
                        yield ": keepalive\n\n"
                        last_keepalive = asyncio.get_running_loop().time()
            finally:
                svc.unregister_change_listener(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)
        api_lifespan = app.router.lifespan_context
        mcp_lifespan = mcp_app.router.lifespan_context

        @asynccontextmanager
        async def combined_lifespan(parent_app):
            async with api_lifespan(parent_app):
                async with mcp_lifespan(mcp_app):
                    yield

        app.router.lifespan_context = combined_lifespan

    web = Path(static_dir or os.environ.get("WEB_DIR") or (Path(__file__).parent / "web"))
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


preview_mcp = None
if os.environ.get("ENABLE_MCP_HTTP") == "1":
    from mcp_server import create_app as create_mcp_app

    preview_mcp = create_mcp_app(
        data_dir=os.environ.get("DATA_DIR"),
        auth_mode=os.environ.get("MCP_AUTH_MODE", "none"),
        streamable_http_path="/",
    )

app = create_app(static_dir=os.environ.get("WEB_DIR"), mcp_app=preview_mcp)
