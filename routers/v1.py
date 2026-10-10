"""Aggregate routers that make up the stable /api/v1 REST surface."""
from fastapi import APIRouter

from routers.v1_assets import router as assets_router
from routers.v1_candidates import router as candidates_router
from routers.v1_files import router as files_router
from routers.v1_imports import router as imports_router
from routers.v1_projects import router as projects_router
from routers.v1_uploads import router as uploads_router

router = APIRouter()
for domain_router in (
    projects_router,
    assets_router,
    candidates_router,
    uploads_router,
    imports_router,
    files_router,
):
    router.include_router(domain_router)
