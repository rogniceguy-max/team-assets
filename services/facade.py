"""Application-wide service facade shared by REST and MCP adapters."""
from core.db import DBMixin
from core.bus import EventBusMixin
from services.base import BaseDomainService
from services.project_service import ProjectMixin
from services.asset_service import AssetMixin
from services.material_service import MaterialMixin
from services.candidate_service import CandidateMixin


class AssetService(
    DBMixin,
    EventBusMixin,
    BaseDomainService,
    ProjectMixin,
    AssetMixin,
    MaterialMixin,
    CandidateMixin,
):
    """Unified service facade aggregating domain services and infrastructure."""

    def __init__(self, data_dir, *, read_only=False):
        super().__init__(data_dir=data_dir, read_only=read_only)
