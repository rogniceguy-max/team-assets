"""Versioned immutable media file access."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from schemas.public import ERROR_RESPONSES

router = APIRouter(prefix="/api/v1", tags=["Files"])


@router.get(
    "/files/{file_id}",
    response_class=FileResponse,
    responses={
        **ERROR_RESPONSES,
        200: {
            "description": "Original media or its JPEG thumbnail",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
def get_file(file_id: str, request: Request, thumbnail: bool = False, download: bool = False):
    service = request.app.state.service
    with service.connect() as c:
        file = service.require(c, "files", file_id)
        material = c.execute(
            "SELECT name FROM materials WHERE file_id=? ORDER BY rowid LIMIT 1", (file_id,)
        ).fetchone()
        candidate = (
            c.execute(
                "SELECT name FROM candidates WHERE file_id=? ORDER BY rowid LIMIT 1", (file_id,)
            ).fetchone()
            if not material
            else None
        )
    filename = (
        material["name"] if material else candidate["name"] if candidate else Path(file["path"]).name
    ).replace("\r", "").replace("\n", "")
    key = "thumbnail" if thumbnail else "path"
    return FileResponse(
        service.root / file[key],
        filename=filename if download else None,
        media_type="image/jpeg" if thumbnail else file["mime"],
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
