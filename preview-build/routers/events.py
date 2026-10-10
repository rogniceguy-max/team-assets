"""Workspace state, file streaming, and SSE event streaming router."""
import asyncio
import json
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter()


@router.get("/api/state")
def state(request: Request):
    return request.app.state.service.state()


@router.get("/files/{file_id}")
def file(file_id: str, request: Request, thumbnail: bool = False, download: bool = False):
    service = request.app.state.service
    with service.connect() as c:
        f = service.require(c, "files", file_id)
        m = c.execute(
            "SELECT name FROM materials WHERE file_id=? ORDER BY rowid LIMIT 1", (file_id,)
        ).fetchone()
        candidate_name = (
            c.execute(
                "SELECT name FROM candidates WHERE file_id=? ORDER BY rowid LIMIT 1", (file_id,)
            ).fetchone()
            if not m
            else None
        )
    filename = (
        m["name"] if m else candidate_name["name"] if candidate_name else Path(f["path"]).name
    ).replace("\r", "").replace("\n", "")
    return FileResponse(
        service.root / f["thumbnail" if thumbnail else "path"],
        filename=filename if download else None,
        media_type="image/jpeg" if thumbnail else f["mime"],
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
