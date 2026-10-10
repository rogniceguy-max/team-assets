"""Project API router."""
from fastapi import APIRouter, Request
from schemas.models import ProjectInput

router = APIRouter()


@router.post("/api/projects", status_code=201)
def project(data: ProjectInput, request: Request):
    return request.app.state.service.create_project(**data.model_dump())


@router.get("/api/trash")
def trash(request: Request):
    return request.app.state.service.trash()


@router.get("/api/history")
def history(request: Request):
    return request.app.state.service.history()
