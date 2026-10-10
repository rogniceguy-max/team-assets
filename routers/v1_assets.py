"""Versioned asset, variant, and material endpoints."""
from typing import Literal

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile

from routers.assets import read_uploads
from schemas.public import (
    AssetCreateRequest,
    AssetDetailResponse,
    AssetPageResponse,
    AssetResponse,
    AssetUpdateRequest,
    DeletePreviewResponse,
    DeleteResponse,
    ERROR_RESPONSES,
    FinalMaterialRequest,
    MaterialMutationResponse,
    MaterialPageResponse,
    MaterialResponse,
    MaterialUploadResponse,
    RestoreResponse,
    VariantPageResponse,
    VariantCreateRequest,
    VariantResponse,
    VariantUpdateRequest,
    VersionRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Assets and materials"])


@router.get("/assets", response_model=AssetPageResponse, responses=ERROR_RESPONSES)
def list_assets(
    request: Request,
    query: str = Query(default="", max_length=255),
    project_id: str | None = None,
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


@router.post("/assets", response_model=AssetResponse, status_code=201, responses=ERROR_RESPONSES)
def create_asset(data: AssetCreateRequest, request: Request):
    return request.app.state.service.create_asset(**data.model_dump())


@router.get("/assets/{asset_id}", response_model=AssetDetailResponse, responses=ERROR_RESPONSES)
def get_asset(asset_id: str, request: Request):
    return request.app.state.service.get_asset(asset_id)


@router.patch("/assets/{asset_id}", response_model=AssetResponse, responses=ERROR_RESPONSES)
def update_asset(asset_id: str, data: AssetUpdateRequest, request: Request):
    return request.app.state.service.update_asset(
        asset_id, **data.model_dump(exclude_unset=True)
    )


@router.post(
    "/assets/{asset_id}/delete-preview",
    response_model=DeletePreviewResponse,
    responses=ERROR_RESPONSES,
)
def preview_asset_deletion(asset_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity("asset", asset_id, data.version, dry_run=True)


@router.delete("/assets/{asset_id}", response_model=DeleteResponse, responses=ERROR_RESPONSES)
def delete_asset(asset_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity("asset", asset_id, data.version)


@router.post("/assets/{asset_id}/restore", response_model=RestoreResponse, responses=ERROR_RESPONSES)
def restore_asset(asset_id: str, request: Request):
    return request.app.state.service.restore_entity("asset", asset_id)


@router.get("/variants", response_model=VariantPageResponse, responses=ERROR_RESPONSES)
def list_variants(
    asset_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    return request.app.state.service.list_variants(
        asset_id, limit, offset, sort_by=sort_by, order=order
    )


@router.post("/variants", response_model=VariantResponse, status_code=201, responses=ERROR_RESPONSES)
def create_variant(data: VariantCreateRequest, request: Request):
    return request.app.state.service.create_variant(**data.model_dump())


@router.get("/variants/{variant_id}", response_model=VariantResponse, responses=ERROR_RESPONSES)
def get_variant(variant_id: str, request: Request):
    return request.app.state.service.get_variant(variant_id)


@router.patch("/variants/{variant_id}", response_model=VariantResponse, responses=ERROR_RESPONSES)
def update_variant(variant_id: str, data: VariantUpdateRequest, request: Request):
    return request.app.state.service.update_variant(
        variant_id, **data.model_dump(exclude_unset=True)
    )


@router.patch(
    "/variants/{variant_id}/final-material",
    response_model=VariantResponse,
    responses=ERROR_RESPONSES,
)
def update_final_material(variant_id: str, data: FinalMaterialRequest, request: Request):
    return request.app.state.service.update_variant(
        variant_id, version=data.version, final_material_id=data.material_id
    )


@router.post(
    "/variants/{variant_id}/delete-preview",
    response_model=DeletePreviewResponse,
    responses=ERROR_RESPONSES,
)
def preview_variant_deletion(variant_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity("variant", variant_id, data.version, dry_run=True)


@router.delete("/variants/{variant_id}", response_model=DeleteResponse, responses=ERROR_RESPONSES)
def delete_variant(variant_id: str, data: VersionRequest, request: Request):
    return request.app.state.service.delete_entity("variant", variant_id, data.version)


@router.post("/variants/{variant_id}/restore", response_model=RestoreResponse, responses=ERROR_RESPONSES)
def restore_variant(variant_id: str, request: Request):
    return request.app.state.service.restore_entity("variant", variant_id)


@router.get("/assets/{asset_id}/materials", response_model=MaterialPageResponse, responses=ERROR_RESPONSES)
def list_asset_materials(
    asset_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name", "created_at"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    return request.app.state.service.list_materials(
        asset_id=asset_id, limit=limit, offset=offset, sort_by=sort_by, order=order
    )


@router.get("/variants/{variant_id}/materials", response_model=MaterialPageResponse, responses=ERROR_RESPONSES)
def list_variant_asset_pool(
    variant_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["position", "name", "created_at"] = "position",
    order: Literal["asc", "desc"] = "asc",
):
    # Variant-scoped lookup intentionally lists the shared asset pool. A material
    # may be selected as final by several variants in the same asset.
    return request.app.state.service.list_materials(
        variant_id=variant_id, limit=limit, offset=offset, sort_by=sort_by, order=order
    )


@router.post(
    "/assets/{asset_id}/materials",
    response_model=MaterialUploadResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def upload_asset_materials(
    asset_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    source: str = Form(default="manual", max_length=100),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return request.app.state.service.add_materials(
        None, read_uploads(files), source, idempotency_key, asset_id=asset_id
    )


@router.post(
    "/variants/{variant_id}/materials",
    response_model=MaterialUploadResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def upload_variant_materials(
    variant_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    source: str = Form(default="manual", max_length=100),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return request.app.state.service.add_materials(
        variant_id, read_uploads(files), source, idempotency_key
    )


@router.get("/materials/{material_id}", response_model=MaterialResponse, responses=ERROR_RESPONSES)
def get_material(material_id: str, request: Request):
    return request.app.state.service.get_material(material_id)


@router.delete(
    "/materials/{material_id}", response_model=MaterialMutationResponse, responses=ERROR_RESPONSES
)
def delete_material(material_id: str, request: Request):
    return request.app.state.service.delete_material(material_id)


@router.post(
    "/materials/{material_id}/restore",
    response_model=MaterialResponse,
    responses=ERROR_RESPONSES,
)
def restore_material(material_id: str, request: Request):
    return request.app.state.service.restore_material(material_id)
