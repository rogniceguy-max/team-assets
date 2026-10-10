"""Pydantic schemas for MCP and batch intake operations."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from episodes import EPISODES_HELP, EPISODE_USAGE_HELP


class UploadImage(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    base64_data: str = Field(description="原始图片字节的标准 Base64，不含 data: 前缀。")


class IntakeAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    category: str = "人物"
    role: str = ""


class IntakeVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: str | None = Field(default=None, description="已有变体 ID；提供时只挂接文件，不修改文字。否则提供 name 创建变体。")
    name: str | None = None
    episodes: str = Field(default="", max_length=2000, description=EPISODES_HELP)
    description: str = ""
    episode_usages: list[dict[str, Any]] = Field(default_factory=list, description=EPISODE_USAGE_HELP)
    file_ids: list[str] = Field(default_factory=list, description="complete_upload 返回的已就绪 file_id。")
    material_names: dict[str, str] = Field(default_factory=dict, description="可选 file_id 到素材显示名称的映射。")
