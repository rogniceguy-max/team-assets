"""Versioned candidate library and folder endpoints."""
from typing import Literal

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile

from schemas.public import (
    CandidateFileCreateRequest,
    CandidateFolderUpdateRequest,
    CandidateFolderListResponse,
    CandidateFolderResponse,
    CandidatePageResponse,
    CandidateReferenceMutationResponse,
    CandidateReferenceCreateRequest,
    CandidateMoveRequest,
    CandidateUpdateRequest,
    CandidateReorderRequest,
    CandidateReorderResponse,
    CandidateResponse,
    CandidateUploadResponse,
    ERROR_RESPONSES,
    MaterialMutationResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Candidate library"])


@router.get("/candidates", response_model=CandidatePageResponse, responses=ERROR_RESPONSES)
def list_candidates(
    project_id: str,
    request: Request,
    query: str = Query(default="", max_length=255),
    category: str | None = Query(default=None, max_length=20),
    status: Literal["active", "disabled", "all"] = "active",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name", "created_at"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    return request.app.state.service.list_candidates(
        project_id,
        query=query,
        category=category,
        status=status,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        order=order,
    )


@router.get("/candidate-folders", response_model=CandidateFolderListResponse, responses=ERROR_RESPONSES)
def list_candidate_folders(
    project_id: str,
    request: Request,
    status: Literal["active", "disabled", "all"] = "all",
):
    return request.app.state.service.list_candidate_folders(project_id, status=status)


@router.patch(
    "/candidate-folders/{folder_id}",
    response_model=CandidateFolderResponse,
    responses=ERROR_RESPONSES,
)
def update_candidate_folder(folder_id: str, data: CandidateFolderUpdateRequest, request: Request):
    return request.app.state.service.update_candidate_folder(
        folder_id, **data.model_dump(exclude_unset=True)
    )


@router.post("/candidate-entries/reorder", response_model=CandidateReorderResponse, responses=ERROR_RESPONSES)
def reorder_candidate_entries(data: CandidateReorderRequest, request: Request):
    return request.app.state.service.reorder_candidate_entries(
        data.project_id,
        data.parent_path,
        [entry.model_dump() for entry in data.entries],
        expected_revision=data.expected_revision,
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateResponse, responses=ERROR_RESPONSES)
def get_candidate(candidate_id: str, request: Request):
    return request.app.state.service.get_candidate(candidate_id)


@router.post("/candidates", response_model=CandidateUploadResponse, status_code=201, responses=ERROR_RESPONSES)
def upload_candidates(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    project_id: str = Form(...),
    category: str = Form(default="其它", max_length=20),
    files: list[UploadFile] = File(...),
):
    uploads = [(file.filename or "素材", file.file, file.content_type) for file in files]
    return request.app.state.service.add_candidate_uploads(
        project_id, uploads, category=category, idempotency_key=idempotency_key
    )


@router.post(
    "/candidates/from-file",
    response_model=CandidateResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def register_candidate_file(data: CandidateFileCreateRequest, request: Request):
    return request.app.state.service.create_candidate_from_file(
        data.project_id,
        data.file_id,
        data.filename,
        category=data.category,
        relative_path=data.relative_path,
        folder_path=data.folder_path,
    )


@router.patch("/candidates/{candidate_id}", response_model=CandidateResponse, responses=ERROR_RESPONSES)
def update_candidate(candidate_id: str, data: CandidateUpdateRequest, request: Request):
    return request.app.state.service.update_candidate(
        candidate_id, **data.model_dump(exclude_unset=True)
    )


@router.post(
    "/candidates/{candidate_id}/move",
    response_model=CandidateResponse,
    responses=ERROR_RESPONSES,
)
def move_candidate(candidate_id: str, data: CandidateMoveRequest, request: Request):
    return request.app.state.service.move_candidate(
        candidate_id, data.version, data.target_folder_path
    )


@router.post(
    "/candidates/{candidate_id}/highlight",
    response_model=CandidateResponse,
    responses=ERROR_RESPONSES,
)
def highlight_candidate(
    candidate_id: str,
    request: Request,
    seconds: int = Query(default=60, ge=1, le=300),
):
    return request.app.state.service.highlight_candidate(candidate_id, seconds=seconds)


@router.post(
    "/candidates/{candidate_id}/references",
    response_model=CandidateReferenceMutationResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_candidate_reference(
    candidate_id: str, data: CandidateReferenceCreateRequest, request: Request
):
    return request.app.state.service.create_candidate_reference(
        candidate_id, **data.model_dump(exclude_unset=True)
    )


@router.delete(
    "/candidates/{candidate_id}/references",
    response_model=MaterialMutationResponse,
    responses=ERROR_RESPONSES,
)
def delete_candidate_reference(
    candidate_id: str,
    request: Request,
    asset_id: str,
    variant_id: str | None = None,
):
    return request.app.state.service.delete_candidate_reference(
        candidate_id, asset_id, variant_id
    )
