"""Candidate library and virtual folder API router."""
from fastapi import APIRouter, File, Form, Header, Request, UploadFile

from schemas.models import (
    CandidateFileInput,
    CandidateFolderPatch,
    CandidateMoveInput,
    CandidatePatch,
    CandidateReferenceInput,
    CandidateReorderInput,
)

router = APIRouter()


@router.get("/api/candidates")
def candidates(
    project_id: str,
    request: Request,
    query: str = "",
    category: str | None = None,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
):
    return request.app.state.service.list_candidates(
        project_id, query=query, category=category, status=status, limit=limit, offset=offset
    )


@router.get("/api/candidate-folders")
def candidate_folders(project_id: str, request: Request, status: str = "all"):
    return request.app.state.service.list_candidate_folders(project_id, status=status)


@router.patch("/api/candidate-folders/{folder_id}")
def update_candidate_folder(folder_id: str, data: CandidateFolderPatch, request: Request):
    return request.app.state.service.update_candidate_folder(
        folder_id, **data.model_dump(exclude_unset=True)
    )


@router.post("/api/candidates/{candidate_id}/move")
def move_candidate(candidate_id: str, data: CandidateMoveInput, request: Request):
    return request.app.state.service.move_candidate(
        candidate_id, data.version, data.target_folder_path
    )


@router.post("/api/candidate-entries/reorder")
def reorder_candidate_entries(data: CandidateReorderInput, request: Request):
    return request.app.state.service.reorder_candidate_entries(
        data.project_id, data.parent_path, [entry.model_dump() for entry in data.entries]
    )


@router.get("/api/candidates/{candidate_id}")
def candidate(candidate_id: str, request: Request):
    return request.app.state.service.get_candidate(candidate_id)


@router.post("/api/candidates", status_code=201)
def upload_candidates(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    project_id: str = Form(...),
    category: str = Form("其它"),
    files: list[UploadFile] = File(...),
):
    uploads = [(file.filename or "素材", file.file, file.content_type) for file in files]
    return request.app.state.service.add_candidate_uploads(
        project_id, uploads, category=category, idempotency_key=idempotency_key
    )


@router.post("/api/candidates/from-file", status_code=201)
def register_candidate_file(data: CandidateFileInput, request: Request):
    return request.app.state.service.create_candidate_from_file(
        data.project_id,
        data.file_id,
        data.filename,
        category=data.category,
        relative_path=data.relative_path,
        folder_path=data.folder_path,
    )


@router.patch("/api/candidates/{candidate_id}")
def update_candidate(candidate_id: str, data: CandidatePatch, request: Request):
    return request.app.state.service.update_candidate(
        candidate_id, **data.model_dump(exclude_unset=True)
    )


@router.post("/api/candidates/{candidate_id}/highlight")
def highlight_candidate(candidate_id: str, request: Request):
    return request.app.state.service.highlight_candidate(candidate_id, seconds=60)


@router.post("/api/candidates/{candidate_id}/references", status_code=201)
def reference_candidate(candidate_id: str, data: CandidateReferenceInput, request: Request):
    return request.app.state.service.create_candidate_reference(
        candidate_id, **data.model_dump(exclude_unset=True)
    )


@router.delete("/api/candidates/{candidate_id}/references")
def unreference_candidate(
    candidate_id: str, asset_id: str, request: Request, variant_id: str | None = None
):
    return request.app.state.service.delete_candidate_reference(candidate_id, asset_id, variant_id)
