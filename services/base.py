"""Base domain service providing validation, tree operations, and entity lookups."""
from fastapi import HTTPException
from core.config import PARENTS, TABLES
from episodes import variant_record


class BaseDomainService:
    """Foundational queries and validation logic shared across domain services."""

    def require(self, c, table, item_id, include_deleted=False):
        if table not in {"projects", "assets", "variants", "materials", "files"}:
            raise ValueError("Unsupported entity table")
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "内容不存在"})
        result = variant_record(row) if table == "variants" else dict(row)
        if not include_deleted:
            if result.get("deleted_at"):
                raise HTTPException(
                    409, {"code": "ENTITY_DELETED", "message": "内容已删除，请先恢复", "current": result}
                )
            if table in PARENTS:
                parent, field = PARENTS[table]
                self.require(c, parent, result[field])
        return result

    def check_version(self, before, version):
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HTTPException(422, "版本号不正确")
        if before["version"] != version:
            raise HTTPException(
                409, {"code": "VERSION_CONFLICT", "message": "内容已更新，请重新读取", "current": before}
            )

    def validate_fields(self, data):
        limits = {
            "name": 200,
            "description": 20000,
            "episodes": 2000,
            "role": 1000,
            "category": 100,
            "source": 100,
        }
        for key, limit in limits.items():
            if key in data and (not isinstance(data[key], str) or len(data[key]) > limit):
                raise HTTPException(422, f"{key} 字段类型或长度不正确")
            if key in {"name", "category"} and key in data and not data[key].strip():
                raise HTTPException(422, f"{key} 不能为空白")

    def _page(self, limit, offset):
        if (
            isinstance(limit, bool)
            or isinstance(offset, bool)
            or not isinstance(limit, int)
            or not isinstance(offset, int)
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise HTTPException(422, "limit 需为 1–200，offset 需为非负整数")

    def _path(self, c, table, row):
        result = variant_record(row) if table == "variants" else dict(row)
        parts = [result["name"]]
        cursor = result
        while table in PARENTS:
            table, field = PARENTS[table]
            cursor = self.require(c, table, cursor[field])
            parts.insert(0, cursor["name"])
        result["path"] = " / ".join(parts)
        return result

    def material(self, row):
        result = dict(row)
        result.update(
            url=f"/files/{result['file_id']}",
            thumbnail_url=f"/files/{result['file_id']}?thumbnail=true",
        )
        return result

    def _subtree(self, c, table, item_id):
        result = {name: [] for name in (*TABLES.values(), "materials")}
        result[table] = [self.require(c, table, item_id, include_deleted=True)]
        ordered = list(result)
        for child in ordered[ordered.index(table) + 1:]:
            parent, field = PARENTS[child]
            ids = [r["id"] for r in result[parent]]
            for parent_id in ids:
                result[child].extend(
                    dict(r) for r in c.execute(f"SELECT * FROM {child} WHERE {field}=?", (parent_id,))
                )
        return result

    def _ensure_restore_parent(self, c, table, before):
        if table in PARENTS:
            parent, field = PARENTS[table]
            try:
                self.require(c, parent, before[field])
            except HTTPException as exc:
                raise HTTPException(
                    409,
                    {
                        "code": "PARENT_DELETED",
                        "message": "请先恢复所属父项",
                        "parent_type": parent[:-1],
                        "parent_id": before[field],
                    },
                ) from exc
