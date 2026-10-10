"""Stable request/response contracts for the public REST API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from episodes import EPISODES_HELP, EPISODE_USAGE_HELP

class PublicResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PublicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionRequest(PublicRequest):
    version: int = Field(ge=1, strict=True)


class FinalMaterialRequest(VersionRequest):
    material_id: str | None = None


class ProjectCreateRequest(PublicRequest):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)


class ProjectUpdateRequest(VersionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)


class AssetCreateRequest(PublicRequest):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="人物", max_length=100)
    role: str = Field(default="", max_length=1000)


class AssetUpdateRequest(VersionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=1000)


class EpisodeUsageInput(PublicRequest):
    episodes: str = Field(min_length=1, max_length=2000, description=EPISODES_HELP)
    mode: str = Field(min_length=1, max_length=50, description=EPISODE_USAGE_HELP)
    note: str = Field(default="", max_length=1000)


class VariantCreateRequest(PublicRequest):
    asset_id: str
    name: str = Field(min_length=1, max_length=200)
    episodes: str = Field(default="", max_length=2000, description=EPISODES_HELP)
    description: str = Field(default="", max_length=20000)
    episode_usages: list[EpisodeUsageInput] = Field(default_factory=list, max_length=200)


class VariantUpdateRequest(VersionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    episodes: str | None = Field(default=None, max_length=2000, description=EPISODES_HELP)
    description: str | None = Field(default=None, max_length=20000)
    episode_usages: list[EpisodeUsageInput] | None = Field(default=None, max_length=200)
    final_material_id: str | None = None


class CandidateUpdateRequest(VersionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=20)
    status: Literal["active", "disabled"] | None = None


class CandidateFolderUpdateRequest(VersionRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "disabled"] | None = None


class CandidateMoveRequest(VersionRequest):
    target_folder_path: str = Field(default="", max_length=1000)


class CandidateReorderEntryRequest(PublicRequest):
    type: Literal["folder", "candidate"]
    id: str = Field(min_length=1, max_length=100)


class CandidateReferenceCreateRequest(PublicRequest):
    asset_id: str
    variant_id: str | None = None
    match_status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, min_length=1, max_length=50)


class CandidateFileCreateRequest(PublicRequest):
    project_id: str
    file_id: str
    filename: str = Field(min_length=1, max_length=1000)
    relative_path: str | None = Field(default=None, max_length=1000)
    folder_path: str | None = Field(default=None, max_length=1000)
    category: str = Field(default="其它", min_length=1, max_length=20)


class ValidationIssue(PublicResponse):
    loc: list[str | int] = Field(default_factory=list)
    msg: str
    type: str


class PublicErrorResponse(PublicResponse):
    detail: str | dict[str, Any] | list[ValidationIssue]


class ProjectResponse(PublicResponse):
    id: str
    name: str
    description: str = ""
    version: int = 1
    deleted_at: str | None = None
    delete_batch: str | None = None


class ProjectPageResponse(PublicResponse):
    projects: list[ProjectResponse]
    total: int
    limit: int
    offset: int


class EpisodeRangeResponse(PublicResponse):
    start: int
    end: int | None = None


class EpisodeScopeResponse(PublicResponse):
    kind: Literal["all", "unknown", "ranges"] = "unknown"
    ranges: list[EpisodeRangeResponse] = Field(default_factory=list)


class EpisodeUsageResponse(PublicResponse):
    episodes: str
    episode_scope: EpisodeScopeResponse
    mode: str
    note: str = ""


class VariantResponse(PublicResponse):
    id: str
    asset_id: str
    name: str
    episodes: str = ""
    description: str = ""
    episode_usages: list[EpisodeUsageResponse] = Field(default_factory=list)
    episode_scope: EpisodeScopeResponse = Field(default_factory=EpisodeScopeResponse)
    episodes_original: str | None = None
    episodes_needs_review: bool = False
    final_material_id: str | None = None
    material_count: int | None = None
    version: int = 1
    deleted_at: str | None = None
    delete_batch: str | None = None
    path: str | None = None


class AssetResponse(PublicResponse):
    id: str
    project_id: str
    name: str
    category: str
    role: str = ""
    version: int = 1
    deleted_at: str | None = None
    delete_batch: str | None = None
    path: str | None = None


class AssetDetailResponse(AssetResponse):
    variants: list[VariantResponse] = Field(default_factory=list)


class AssetPageResponse(PublicResponse):
    assets: list[AssetResponse]
    total: int
    limit: int
    offset: int


class VariantPageResponse(PublicResponse):
    variants: list[VariantResponse]
    total: int
    limit: int
    offset: int
    asset_id: str


class MaterialResponse(PublicResponse):
    id: str
    asset_id: str
    variant_id: str | None = None
    candidate_id: str | None = None
    file_id: str
    name: str
    source: str
    created_at: str | None = None
    deleted_at: str | None = None
    delete_batch: str | None = None
    version: int = 1
    mime: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    url: str
    thumbnail_url: str
    path: str | None = None


class MaterialPageResponse(PublicResponse):
    materials: list[MaterialResponse]
    total: int
    limit: int
    offset: int


class MaterialUploadResponse(PublicResponse):
    materials: list[MaterialResponse]


class CandidateReferenceResponse(PublicResponse):
    id: str
    candidate_id: str
    asset_id: str
    variant_id: str | None = None
    created_at: str
    match_status: str = "unreviewed"
    confidence: float | None = None
    note: str = ""
    source: str = "mcp"


class CandidateResponse(PublicResponse):
    id: str
    project_id: str
    file_id: str
    name: str
    relative_path: str = ""
    folder_path: str = ""
    sort_order: float = 0
    category: str = "其它"
    status: str = "active"
    disabled: bool = False
    disabled_by_folder: bool = False
    highlighted: bool = False
    highlighted_until: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None
    delete_batch: str | None = None
    version: int = 1
    mime: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    url: str
    thumbnail_url: str
    references: list[CandidateReferenceResponse] = Field(default_factory=list)


class CandidatePageResponse(PublicResponse):
    candidates: list[CandidateResponse]
    total: int
    limit: int
    offset: int
    project_id: str
    status: Literal["active", "disabled", "all"]
    query: str
    category: str | None = None


class CandidateFolderResponse(PublicResponse):
    id: str
    project_id: str
    path: str
    parent_path: str = ""
    name: str
    status: str = "active"
    sort_order: float = 0
    created_at: str | None = None
    updated_at: str | None = None
    version: int = 1
    disabled: bool = False
    disabled_by_parent: bool = False


class CandidateFolderListResponse(PublicResponse):
    folders: list[CandidateFolderResponse]
    project_id: str
    status: Literal["active", "disabled", "all"]


class CandidateUploadResponse(PublicResponse):
    candidates: list[CandidateResponse]


class CandidateReferenceMutationResponse(PublicResponse):
    reference: CandidateReferenceResponse
    candidate: CandidateResponse
    material: MaterialResponse


class CandidateReorderRequest(PublicRequest):
    project_id: str
    parent_path: str = ""
    expected_revision: int = Field(ge=0)
    entries: list[CandidateReorderEntryRequest] = Field(min_length=1)


class CandidateReorderResponse(PublicResponse):
    project_id: str
    parent_path: str
    entries: list[dict[str, Any]]


class DeletePreviewResponse(PublicResponse):
    dry_run: Literal[True]
    entity_type: Literal["project", "asset", "variant"]
    id: str
    affected: dict[str, int]
    batch_id: None = None


class DeleteResponse(PublicResponse):
    ok: bool
    batch_id: str
    affected: dict[str, int]


class RestoreResponse(PublicResponse):
    ok: bool
    affected: dict[str, int]
    entity: dict[str, Any] | None = None


class MaterialMutationResponse(PublicResponse):
    ok: bool


class RevisionResponse(PublicResponse):
    revision: int


class HistoryEventResponse(PublicResponse):
    revision: int
    action: str
    object_id: str
    details: dict[str, Any]
    created_at: str


class HistoryPageResponse(PublicResponse):
    events: list[HistoryEventResponse]
    total: int
    limit: int
    offset: int
    action: str | None = None


class TrashItemResponse(PublicResponse):
    entity_type: Literal["project", "asset", "variant", "material"]
    id: str
    name: str
    deleted_at: str
    delete_batch: str | None = None
    version: int = 1
    parent_id: str | None = None


class TrashPageResponse(PublicResponse):
    items: list[TrashItemResponse]
    total: int
    limit: int
    offset: int
    entity_type: str | None = None


class UploadBeginRequest(PublicRequest):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern="^[a-fA-F0-9]{64}$")
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class UploadSessionResponse(PublicResponse):
    id: str
    filename: str
    size: int
    offset: int
    status: str
    file_id: str | None = None
    expires_at: float
    upload_token: str
    upload_url: str
    chunk_size: int


class UploadStatusResponse(PublicResponse):
    id: str
    filename: str
    size: int
    offset: int
    status: str
    file_id: str | None = None
    expires_at: float
    expired: bool = False


class IntakeAssetRequest(PublicRequest):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="人物", max_length=100)
    role: str = Field(default="", max_length=1000)


class IntakeVariantRequest(PublicRequest):
    variant_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    episodes: str = Field(default="", max_length=2000, description=EPISODES_HELP)
    description: str = ""
    episode_usages: list[EpisodeUsageInput] = Field(default_factory=list, max_length=200)
    file_ids: list[str] = Field(default_factory=list)
    material_names: dict[str, str] = Field(default_factory=dict)


class IntakeCommitRequest(PublicRequest):
    project_id: str
    variants: list[IntakeVariantRequest] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    asset_id: str | None = None
    asset: IntakeAssetRequest | None = None


class IntakeCommitResponse(PublicResponse):
    asset: AssetResponse
    variants: list[VariantResponse]
    materials: list[MaterialResponse]


class CandidateMetadataInput(PublicRequest):
    match_status: str = "unreviewed"
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str = Field(default="", max_length=1000)
    source: str = Field(default="mcp", min_length=1, max_length=50)


class ProjectImportVariantInput(PublicRequest):
    variant_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    episodes: str = Field(default="", max_length=2000, description=EPISODES_HELP)
    description: str = ""
    episode_usages: list[EpisodeUsageInput] = Field(default_factory=list, max_length=200)
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    material_names: dict[str, str] = Field(default_factory=dict)
    candidate_ids: list[str] = Field(default_factory=list, max_length=100)
    candidate_metadata: dict[str, CandidateMetadataInput] = Field(default_factory=dict)


class ProjectImportAssetInput(PublicRequest):
    asset_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    role: str | None = Field(default=None, max_length=1000)
    variants: list[ProjectImportVariantInput] = Field(min_length=1, max_length=200)


class ProjectImportBase(PublicRequest):
    project_id: str
    assets: list[ProjectImportAssetInput] = Field(min_length=1, max_length=200)
    on_name_conflict: Literal["error", "reuse"] = "error"


class ProjectImportApplyRequest(ProjectImportBase):
    idempotency_key: str = Field(min_length=1, max_length=200)


class ProjectImportPlanResponse(PublicResponse):
    valid: bool
    project_id: str
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    summary: dict[str, int]
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ProjectImportApplyResponse(PublicResponse):
    project_id: str
    assets: list[AssetResponse]
    variants: list[VariantResponse]
    materials: list[MaterialResponse]
    references: list[CandidateReferenceResponse]
    warnings: list[dict[str, Any]]


class CoverageReportResponse(PublicResponse):
    project: ProjectResponse
    assets: list[dict[str, Any]]
    summary: dict[str, Any]
    include_disabled: bool


ERROR_RESPONSES = {
    400: {"model": PublicErrorResponse, "description": "请求格式或参数不正确"},
    403: {"model": PublicErrorResponse, "description": "来源未获信任或服务为只读"},
    404: {"model": PublicErrorResponse, "description": "目标不存在"},
    409: {"model": PublicErrorResponse, "description": "版本、幂等键或当前状态冲突"},
    413: {"model": PublicErrorResponse, "description": "请求或文件超出限制"},
    422: {"model": PublicErrorResponse, "description": "请求字段未通过校验"},
    507: {"model": PublicErrorResponse, "description": "存储空间不足"},
}
