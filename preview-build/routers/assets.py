"""Asset, variant, and material API router."""
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile

from core.config import MAX_UPLOAD
from schemas.models import AssetInput, AssetPatch, VariantInput, VariantPatch

router = APIRouter()


def read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if len(files) > 20:
        raise HTTPException(422, "每次最多上传 20 张")
    uploads = []
    total = 0
    for f in files:
        body = f.file.read(MAX_UPLOAD + 1)
        total += len(body)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "每次上传总量不能超过 30 MB")
        uploads.append((f.filename or "图片", body))
    return uploads


@router.post("/api/assets", status_code=201)
def asset(data: AssetInput, request: Request):
    return request.app.state.service.create_asset(**data.model_dump())


@router.patch("/api/assets/{asset_id}")
def update_asset(asset_id: str, data: AssetPatch, request: Request):
    return request.app.state.service.update_asset(asset_id, **data.model_dump(exclude_unset=True))


@router.post("/api/variants", status_code=201)
def variant(data: VariantInput, request: Request):
    return request.app.state.service.create_variant(**data.model_dump())


@router.patch("/api/variants/{variant_id}")
def update_variant(variant_id: str, data: VariantPatch, request: Request):
    return request.app.state.service.update_variant(variant_id, **data.model_dump(exclude_unset=True))


@router.post("/api/variants/{variant_id}/materials", status_code=201)
def upload_variant_materials(
    variant_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    source: str = Form("manual"),
    idempotency_key: str | None = Header(default=None),
):
    return request.app.state.service.add_materials(
        variant_id, read_uploads(files), source, idempotency_key
    )


@router.post("/api/assets/{asset_id}/materials", status_code=201)
def upload_asset_materials(
    asset_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    source: str = Form("manual"),
    idempotency_key: str | None = Header(default=None),
):
    return request.app.state.service.add_materials(
        None, read_uploads(files), source, idempotency_key, asset_id=asset_id
    )


@router.get("/api/assets/{asset_id}/materials")
def asset_materials(asset_id: str, request: Request, limit: int = 50, offset: int = 0):
    return request.app.state.service.list_materials(asset_id=asset_id, limit=limit, offset=offset)


@router.delete("/api/materials/{material_id}")
def delete_material(material_id: str, request: Request):
    return request.app.state.service.delete_material(material_id)


@router.post("/api/materials/{material_id}/restore")
def restore_material(material_id: str, request: Request):
    return request.app.state.service.restore_material(material_id)
