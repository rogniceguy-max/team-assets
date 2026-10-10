"""Versioned project, trash, history, and coverage endpoints."""
from typing import Literal

from fastapi import APIRouter, Query, Request

from schemas.public import (
    AssetPageResponse,
    CoverageReportResponse,
    DeletePreviewResponse,
    DeleteResponse,
    ERROR_RESPONSES,
    HistoryPageResponse,
    ProjectPageResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    RestoreResponse,
    TrashPageResponse,
    VersionRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Projects and activity"])


@router.get("/projects", response_model=ProjectPageResponse, responses=ERROR_RESPONSES)
def list_projects(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    return request.app.state.service.list_projects(limit, offset, sort_by=sort_by, order=order)


@router.post("/projects", response_model=ProjectResponse, status_code=201, responses=ERROR_RESPONSES)
def create_project(data: ProjectCreateRequest, request: Request):
    return request.app.state.service.create_project(**data.model_dump())


@router.get("/projects/{project_id}", response_model=ProjectResponse, responses=ERROR_RESPONSES)
def get_project(project_id: str, request: Request):
    return request.app.state.service.get_project(project_id)


@router.patch("/projects/{project_id}", response_model=ProjectResponse, responses=ERROR_RESPONSES)
def update_project(project_id: str, data: ProjectUpdateRequest, request: Request):
    return request.app.state.service.update_project(
        project_id, **data.model_dump(exclude_unset=True)
    )


@router.get("/projects/{project_id}/assets", response_model=AssetPageResponse, responses=ERROR_RESPONSES)
def list_project_assets(
    project_id: str,
    request: Request,
    query: str = Query(default="", max_length=255),
    category: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    return request.app.state.service.search_assets(
        query=query,
        project_id=project_id,
        category=category,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        order=order,
    )


@router.post(
    "/projects/{project_id}/delete-preview",
    response_model=DeletePreviewResponse,
    responses=ERROR_RESPONSES,
)
def preview_project_deletion(project_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity(
        "project", project_id, data.version, dry_run=True
    )


@router.delete("/projects/{project_id}", response_model=DeleteResponse, responses=ERROR_RESPONSES)
def delete_project(project_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity("project", project_id, data.version)


@router.post("/projects/{project_id}/restore", response_model=RestoreResponse, responses=ERROR_RESPONSES)
def restore_project(project_id: str, request: Request):
    return request.app.state.service.restore_entity("project", project_id)


@router.get("/trash", response_model=TrashPageResponse, responses=ERROR_RESPONSES)
def list_trash(
    request: Request,
    entity_type: Literal["project", "asset", "variant", "material"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return request.app.state.service.list_trash(entity_type, limit, offset)


@router.get("/history", response_model=HistoryPageResponse, responses=ERROR_RESPONSES)
def list_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=100),
):
    return request.app.state.service.list_history(limit, offset, action)


@router.get(
    "/projects/{project_id}/coverage",
    response_model=CoverageReportResponse,
    responses=ERROR_RESPONSES,
)
def project_coverage(project_id: str, request: Request, include_disabled: bool = False):
    return request.app.state.service.audit_project_asset_coverage(project_id, include_disabled)
