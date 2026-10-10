"""Shared asset API. Database and immutable image files live under DATA_DIR."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.utils import now, uid  # Compatibility exports used by older local scripts/tests.
from routers.assets import router as assets_router
from routers.candidates import router as candidates_router
from routers.events import router as events_router
from routers.projects import router as projects_router
from services.facade import AssetService


def _normalize_origin(value):
    try:
        parsed = urlsplit(value.strip())
    except (AttributeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _cors_origins_from_environment():
    values = os.environ.get("TEAM_ASSETS_CORS_ORIGINS", "")
    origins = set()
    for value in values.split(","):
        value = value.strip()
        if not value:
            continue
        origin = _normalize_origin(value)
        if origin is None:
            raise RuntimeError(
                "TEAM_ASSETS_CORS_ORIGINS must be a comma-separated list of exact http(s) origins"
            )
        origins.add(origin)
    return origins


def create_app(data_dir=None, static_dir=None, mcp_app=None, *, service=None, read_only=None):
    app = FastAPI(title="Team Assets", version="0.1.0")
    allowed_origins = _cors_origins_from_environment()
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
    configured_read_only = bool(getattr(service, "read_only", configured_read_only))
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
    async def origin_and_read_only_policy(request, call_next):
        if (
            configured_read_only
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not request.url.path.startswith("/mcp")
        ):
            return JSONResponse(
                {"detail": {"code": "READ_ONLY", "message": "此服务当前为只读预览，不能修改数据"}},
                status_code=403,
            )
        origin = request.headers.get("origin")
        if origin:
            normalized = _normalize_origin(origin)
            request_origin = _normalize_origin(
                f"{request.url.scheme}://{request.headers.get('host', '')}"
            )
            if normalized is None or (normalized != request_origin and normalized not in allowed_origins):
                return JSONResponse(
                    {"detail": {"code": "ORIGIN_NOT_ALLOWED", "message": "此浏览器来源未列入可信前端白名单"}},
                    status_code=403,
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "If-None-Match",
            "Last-Event-ID",
            "MCP-Protocol-Version",
            "Range",
            "X-Upload-Token",
        ],
        expose_headers=[
            "Content-Disposition",
            "ETag",
            "Last-Modified",
            "Location",
            "X-Team-Assets-Client-SHA256",
            "X-Team-Assets-Client-Version",
        ],
        max_age=600,
    )

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


def _default_app():
    data_dir = os.environ.get("DATA_DIR", str(Path(__file__).parent / "data"))
    read_only = os.environ.get("TEAM_ASSETS_READ_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
    service = AssetService(data_dir, read_only=read_only)
    preview_mcp = None
    if os.environ.get("ENABLE_MCP_HTTP") == "1":
        from mcp_server import create_app as create_mcp_app

        preview_mcp = create_mcp_app(
            service=service,
            auth_mode=os.environ.get("MCP_AUTH_MODE", "none"),
            streamable_http_path="/",
        )
    return create_app(service=service, static_dir=os.environ.get("WEB_DIR"), mcp_app=preview_mcp)


app = _default_app()
