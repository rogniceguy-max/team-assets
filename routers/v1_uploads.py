"""Versioned resumable upload and atomic intake endpoints."""
from fastapi import APIRouter, Header, Query, Request
from intake import IntakeService
from schemas.public import (
    ERROR_RESPONSES,
    IntakeCommitRequest,
    IntakeCommitResponse,
    UploadBeginRequest,
    UploadSessionResponse,
    UploadStatusResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Uploads and intake"])


@router.post("/uploads", response_model=UploadSessionResponse, status_code=201, responses=ERROR_RESPONSES)
def begin_upload(data: UploadBeginRequest, request: Request):
    uploads = request.app.state.uploads
    result = uploads.begin_upload(
        data.filename,
        data.size,
        sha256=data.sha256,
        idempotency_key=data.idempotency_key,
    )
    result["upload_url"] = f"/api/v1/uploads/{result['id']}"
    result["chunk_size"] = uploads.CHUNK_SIZE
    return result


@router.get(
    "/uploads/{upload_id}", response_model=UploadStatusResponse, responses=ERROR_RESPONSES
)
def get_upload_status(
    upload_id: str,
    request: Request,
    upload_token: str | None = Header(default=None, alias="X-Upload-Token"),
):
    return request.app.state.uploads.status_for_token(upload_id, upload_token)


@router.put(
    "/uploads/{upload_id}",
    response_model=UploadStatusResponse,
    responses=ERROR_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0, description="当前已接收的字节偏移量"),
):
    # UploadManager owns chunk validation, session locking, retries, and offsets.
    return await request.app.state.uploads.http(request)


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=UploadStatusResponse,
    responses=ERROR_RESPONSES,
)
def complete_upload(
    upload_id: str,
    request: Request,
    upload_token: str | None = Header(default=None, alias="X-Upload-Token"),
):
    return request.app.state.uploads.complete_upload(upload_id, token=upload_token or "")


@router.delete(
    "/uploads/{upload_id}", response_model=UploadStatusResponse, responses=ERROR_RESPONSES
)
def cancel_upload(
    upload_id: str,
    request: Request,
    upload_token: str | None = Header(default=None, alias="X-Upload-Token"),
):
    return request.app.state.uploads.cancel_upload(upload_id, token=upload_token or "")


@router.post(
    "/intake/commit",
    response_model=IntakeCommitResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def commit_intake(data: IntakeCommitRequest, request: Request):
    intake = request.app.state.intake or IntakeService(request.app.state.service)
    return intake.commit_intake(
        project_id=data.project_id,
        variants=[item.model_dump(exclude_unset=True) for item in data.variants],
        idempotency_key=data.idempotency_key,
        asset_id=data.asset_id,
        asset=data.asset.model_dump(exclude_unset=True) if data.asset else None,
    )
