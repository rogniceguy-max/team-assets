"""Versioned project manifest planning and atomic application."""
from fastapi import APIRouter, Request

from intake import IntakeService
from schemas.public import (
    ERROR_RESPONSES,
    ProjectImportApplyRequest,
    ProjectImportApplyResponse,
    ProjectImportBase,
    ProjectImportPlanResponse,
)

router = APIRouter(prefix="/api/v1/project-imports", tags=["Project import"])


def _manifest(data: ProjectImportBase):
    return [
        {
            **asset.model_dump(exclude_unset=True, exclude={"variants"}),
            "variants": [variant.model_dump(exclude_unset=True) for variant in asset.variants],
        }
        for asset in data.assets
    ]


@router.post("/plan", response_model=ProjectImportPlanResponse, responses=ERROR_RESPONSES)
def plan_project_import(data: ProjectImportBase, request: Request):
    intake = IntakeService(request.app.state.service)
    return intake.plan_project_import(
        data.project_id,
        _manifest(data),
        on_name_conflict=data.on_name_conflict,
    )


@router.post(
    "/apply",
    response_model=ProjectImportApplyResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def apply_project_import(data: ProjectImportApplyRequest, request: Request):
    intake = IntakeService(request.app.state.service)
    return intake.apply_project_import(
        data.project_id,
        _manifest(data),
        data.idempotency_key,
        on_name_conflict=data.on_name_conflict,
    )
