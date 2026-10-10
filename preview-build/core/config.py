"""Constants, limits, and domain definitions."""

MAX_UPLOAD = 30 * 1024 * 1024

FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}

CANDIDATE_CATEGORIES = {"人物", "场景", "道具", "其它"}
CANDIDATE_STATUSES = {"active", "disabled"}
CANDIDATE_MATCH_STATUSES = {
    "unreviewed", "folder", "web", "exact", "approximate", "partial", "missing"
}

TABLES = {"project": "projects", "asset": "assets", "variant": "variants"}
PARENTS = {
    "assets": ("projects", "project_id"),
    "variants": ("assets", "asset_id"),
    "materials": ("assets", "asset_id"),
}
CHILDREN = {
    "projects": [("assets", "project_id")],
    "assets": [("variants", "asset_id"), ("materials", "asset_id")],
}
