"""Configuration constants and safe defaults for Team Assets MCP client."""

DEFAULT_PROTOCOL_VERSION = "2025-11-25"
DEFAULT_ENDPOINT = "http://192.168.10.28:8093/mcp"
DEFAULT_TIMEOUT = 180.0
DEFAULT_RETRIES = 3
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504, 507}
SAFE_READ_METHODS = {"initialize", "tools/list", "resources/list", "prompts/list"}
SAFE_READ_TOOLS = {
    "get_workspace",
    "list_projects",
    "search_assets",
    "get_asset",
    "get_variant",
    "list_materials",
    "list_candidates",
    "list_candidate_folders",
    "get_candidate",
    "list_trash",
    "list_history",
    "preview_deletion",
    "get_material_preview",
    "get_capabilities",
    "audit_project_asset_coverage",
    "plan_project_import",
    "get_upload_status",
    "get_skill_manifest",
}
