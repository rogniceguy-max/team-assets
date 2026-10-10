"""Pydantic request and patch models for REST API."""
from pydantic import BaseModel, Field


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)


class AssetInput(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="人物", max_length=100)
    role: str = Field(default="", max_length=1000)


class AssetPatch(BaseModel):
    model_config = {"extra": "forbid"}
    version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = Field(default=None, max_length=1000)


class VariantInput(BaseModel):
    asset_id: str
    name: str = Field(min_length=1, max_length=200)
    episodes: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=20000)
    episode_usages: list[dict] = Field(default_factory=list, max_length=200)


class VariantPatch(BaseModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    episodes: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=20000)
    episode_usages: list[dict] | None = Field(default=None, max_length=200)
    final_material_id: str | None = None


class CandidatePatch(BaseModel):
    model_config = {"extra": "forbid"}
    version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=20)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class CandidateFolderPatch(BaseModel):
    model_config = {"extra": "forbid"}
    version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class CandidateMoveInput(BaseModel):
    version: int = Field(ge=1, strict=True)
    target_folder_path: str = Field(default="", max_length=1000)


class CandidateReorderEntry(BaseModel):
    type: str = Field(pattern="^(folder|candidate)$")
    id: str = Field(min_length=1, max_length=100)


class CandidateReorderInput(BaseModel):
    project_id: str
    parent_path: str = Field(default="", max_length=1000)
    entries: list[CandidateReorderEntry]


class CandidateReferenceInput(BaseModel):
    asset_id: str
    variant_id: str | None = None
    match_status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, min_length=1, max_length=50)


class CandidateFileInput(BaseModel):
    project_id: str
    file_id: str
    filename: str = Field(min_length=1, max_length=1000)
    relative_path: str | None = Field(default=None, max_length=1000)
    folder_path: str | None = Field(default=None, max_length=1000)
    category: str = Field(default="其它", min_length=1, max_length=20)
