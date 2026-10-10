"""Candidate library catalogue, virtual folder tree, and asset references."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException

from core.config import CANDIDATE_CATEGORIES, CANDIDATE_MATCH_STATUSES, CANDIDATE_STATUSES
from core.utils import clean_relative_path, now, uid
from media_utils import inspect_media


class CandidateMixin:
    """Project-scoped candidate catalogue and virtual folder tree."""

    @staticmethod
    def _folder_path_from_relative(relative_path):
        raw = str(relative_path or "").replace("\\", "/")
        parts = [part for part in raw.split("/") if part not in ("", ".", "..")]
        return "/".join(parts[:-1]) if len(parts) > 1 else ""

    @staticmethod
    def _relative_name(relative_path):
        raw = str(relative_path or "").replace("\\", "/")
        parts = [part for part in raw.split("/") if part not in ("", ".", "..")]
        return parts[-1] if parts else "素材"

    @staticmethod
    def _normalize_folder_path(path):
        raw = str(path or "").replace("\\", "/").strip("/")
        parts = [part.strip() for part in raw.split("/") if part.strip() and part not in (".", "..")]
        if any("\x00" in part or "\r" in part or "\n" in part for part in parts):
            raise HTTPException(422, "文件夹路径包含非法字符")
        result = "/".join(parts)
        if len(result) > 1000:
            raise HTTPException(422, "文件夹路径过长")
        return result

    @staticmethod
    def _normalize_folder_name(name):
        value = str(name or "").strip()
        if (
            not value
            or len(value) > 255
            or "/" in value
            or "\\" in value
            or value in (".", "..")
            or "\x00" in value
            or "\r" in value
            or "\n" in value
        ):
            raise HTTPException(422, "文件夹名称不合法")
        return value

    def _next_candidate_order(self, c, project_id, parent_path):
        candidate_max = c.execute(
            "SELECT MAX(sort_order) FROM candidates WHERE project_id=? AND folder_path=? AND deleted_at IS NULL",
            (project_id, parent_path),
        ).fetchone()[0]
        folder_max = c.execute(
            "SELECT MAX(sort_order) FROM candidate_folders WHERE project_id=? AND parent_path=?",
            (project_id, parent_path),
        ).fetchone()[0]
        values = [float(value) for value in (candidate_max, folder_max) if value is not None]
        return (max(values) + 1.0) if values else 0.0

    def _allocate_candidate_sort_order(self, c, project_id, parent_path, next_orders=None):
        if next_orders is None:
            return self._next_candidate_order(c, project_id, parent_path)
        if parent_path not in next_orders:
            next_orders[parent_path] = self._next_candidate_order(c, project_id, parent_path)
        order = next_orders[parent_path]
        next_orders[parent_path] = order + 1.0
        return order

    def _ensure_folder_records(self, c, project_id, folder_path, next_orders=None):
        path = self._normalize_folder_path(folder_path)
        if not path:
            return
        parent = ""
        for segment in path.split("/"):
            current = f"{parent}/{segment}" if parent else segment
            row = c.execute(
                "SELECT id FROM candidate_folders WHERE project_id=? AND path=?", (project_id, current)
            ).fetchone()
            if row is None:
                timestamp = now()
                c.execute(
                    "INSERT INTO candidate_folders(id,project_id,path,parent_path,name,status,sort_order,created_at,updated_at,version) VALUES(?,?,?,?,?,'active',?,?,?,1)",
                    (
                        uid(),
                        project_id,
                        current,
                        parent,
                        segment,
                        self._allocate_candidate_sort_order(c, project_id, parent, next_orders),
                        timestamp,
                        timestamp,
                    ),
                )
            parent = current

    def _disabled_folder_paths(self, c, project_id):
        return [
            row["path"]
            for row in c.execute(
                "SELECT path FROM candidate_folders WHERE project_id=? AND status='disabled'",
                (project_id,),
            )
        ]

    @staticmethod
    def _folder_inherited_disabled(folder_path, disabled_paths):
        return any(folder_path == path or folder_path.startswith(path + "/") for path in disabled_paths)

    def _folder_effective_disabled(self, c, row):
        path = row["path"] if isinstance(row, sqlite3.Row) else row.get("path", "")
        disabled_paths = self._disabled_folder_paths(
            c, row["project_id"] if isinstance(row, sqlite3.Row) else row["project_id"]
        )
        own = path in disabled_paths
        inherited = any(path != disabled and path.startswith(disabled + "/") for disabled in disabled_paths)
        return own or inherited, inherited

    def _prune_empty_folders(self, c, project_id):
        rows = c.execute(
            "SELECT id,path FROM candidate_folders WHERE project_id=? ORDER BY LENGTH(path) DESC", (project_id,)
        ).fetchall()
        for row in rows:
            exists = c.execute(
                "SELECT 1 FROM candidates WHERE project_id=? AND deleted_at IS NULL AND (folder_path=? OR substr(folder_path, 1, length(?) + 1) = ? || '/') LIMIT 1",
                (project_id, row["path"], row["path"], row["path"]),
            ).fetchone()
            if not exists:
                c.execute("DELETE FROM candidate_folders WHERE id=?", (row["id"],))

    def _folder_record(self, row, *, disabled=False, inherited=False):
        result = dict(row)
        result["disabled"] = bool(disabled)
        result["disabled_by_parent"] = bool(inherited)
        return result

    def candidate(self, row, *, inherited_disabled=False):
        result = dict(row)
        own_disabled = result.get("status") == "disabled"
        result.update(
            url=f"/files/{result['file_id']}",
            thumbnail_url=f"/files/{result['file_id']}?thumbnail=true",
            disabled=own_disabled or inherited_disabled,
            disabled_by_folder=bool(inherited_disabled),
            folder_path=result.get("folder_path")
            or self._folder_path_from_relative(result.get("relative_path", "")),
        )
        highlighted_until = result.get("highlighted_until")
        result["highlighted"] = bool(highlighted_until and highlighted_until > now())
        return result

    @staticmethod
    def _candidate_status(status):
        if status not in CANDIDATE_STATUSES:
            raise HTTPException(422, "候选素材状态只能是 active 或 disabled")
        return status

    def _candidate_row(self, c, candidate_id, include_deleted=False):
        row = c.execute(
            "SELECT ca.*,f.mime,f.size,f.width,f.height,f.sha256 "
            "FROM candidates ca JOIN files f ON f.id=ca.file_id WHERE ca.id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "候选素材不存在"})
        inherited = self._folder_inherited_disabled(
            row["folder_path"] or self._folder_path_from_relative(row["relative_path"]),
            self._disabled_folder_paths(c, row["project_id"]),
        )
        result = self.candidate(row, inherited_disabled=inherited)
        if result.get("deleted_at") and not include_deleted:
            raise HTTPException(409, {"code": "ENTITY_DELETED", "message": "候选素材已删除，请先恢复", "current": result})
        if not include_deleted:
            self.require(c, "projects", result["project_id"])
        return result

    def list_candidates(
        self, project_id, query="", category=None, status="active", limit=50, offset=0,
        sort_by="position", order="asc",
    ):
        self._page(limit, offset)
        sort_columns = {
            "position": "ca.folder_path,ca.sort_order,ca.rowid",
            "name": "ca.folder_path,ca.name COLLATE NOCASE,ca.sort_order,ca.rowid",
            "created_at": "ca.folder_path,ca.created_at,ca.sort_order,ca.rowid",
        }
        if sort_by not in sort_columns or order not in {"asc", "desc"}:
            raise HTTPException(422, "候选素材排序字段或方向不正确")
        ordering = ",".join(f"{column} {order.upper()}" for column in sort_columns[sort_by].split(","))
        if not isinstance(project_id, str) or not project_id:
            raise HTTPException(422, "project_id 不能为空")
        if not isinstance(query, str) or len(query) > 255:
            raise HTTPException(422, "搜索文字过长")
        if category is not None and category not in CANDIDATE_CATEGORIES:
            raise HTTPException(422, "候选素材分类不正确")
        if status not in {"active", "disabled", "all"}:
            raise HTTPException(422, "status 只能是 active、disabled 或 all")
        with self.connect() as c:
            self.require(c, "projects", project_id)
            filters = ["ca.project_id=?", "ca.deleted_at IS NULL"]
            values = [project_id]
            if category is not None:
                filters.append("ca.category=?")
                values.append(category)
            if query:
                filters.append("instr(lower(ca.name),lower(?)) > 0")
                values.append(query)
            inherited_disabled_sql = (
                "EXISTS (SELECT 1 FROM candidate_folders cf "
                "WHERE cf.project_id=ca.project_id AND cf.status='disabled' "
                "AND (ca.folder_path=cf.path OR "
                "substr(ca.folder_path,1,length(cf.path)+1)=cf.path || '/'))"
            )
            if status == "active":
                filters.append(f"ca.status='active' AND NOT ({inherited_disabled_sql})")
            elif status == "disabled":
                filters.append(f"(ca.status='disabled' OR ({inherited_disabled_sql}))")
            where = " AND ".join(filters)
            base = "FROM candidates ca JOIN files f ON f.id=ca.file_id WHERE " + where
            total = c.execute("SELECT COUNT(*) " + base, values).fetchone()[0]
            rows = c.execute(
                "SELECT ca.*,f.mime,f.size,f.width,f.height,f.sha256, "
                f"({inherited_disabled_sql}) AS _inherited_disabled "
                + base
                + f" ORDER BY {ordering} LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
            candidates = []
            cand_map = {}
            for row in rows:
                row_data = dict(row)
                inherited = bool(row_data.pop("_inherited_disabled"))
                item = self.candidate(row_data, inherited_disabled=inherited)
                item["references"] = []
                cand_map[item["id"]] = item
                candidates.append(item)

            if cand_map:
                placeholders = ",".join("?" for _ in cand_map)
                refs = c.execute(
                    "SELECT cr.* FROM candidate_references cr "
                    "JOIN assets a ON a.id=cr.asset_id JOIN projects p ON p.id=a.project_id "
                    "LEFT JOIN variants v ON v.id=cr.variant_id "
                    f"WHERE cr.candidate_id IN ({placeholders}) AND a.deleted_at IS NULL AND p.deleted_at IS NULL "
                    "AND (cr.variant_id IS NULL OR v.deleted_at IS NULL) ORDER BY cr.rowid",
                    list(cand_map.keys()),
                ).fetchall()
                for reference in refs:
                    if reference["candidate_id"] in cand_map:
                        cand_map[reference["candidate_id"]]["references"].append(dict(reference))

            return {
                "candidates": candidates,
                "total": total,
                "limit": limit,
                "offset": offset,
                "project_id": project_id,
                "status": status,
                "query": query,
                "category": category,
            }

    def get_candidate(self, candidate_id):
        with self.connect() as c:
            result = self._candidate_row(c, candidate_id)
            result["references"] = [
                dict(r)
                for r in c.execute(
                    "SELECT cr.* FROM candidate_references cr "
                    "JOIN assets a ON a.id=cr.asset_id JOIN projects p ON p.id=a.project_id "
                    "LEFT JOIN variants v ON v.id=cr.variant_id "
                    "WHERE cr.candidate_id=? AND a.deleted_at IS NULL AND p.deleted_at IS NULL "
                    "AND (cr.variant_id IS NULL OR v.deleted_at IS NULL) ORDER BY cr.rowid",
                    (candidate_id,),
                )
            ]
            return result

    def update_candidate(self, candidate_id, version, *, name=None, category=None, status=None):
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HTTPException(422, "版本号不正确")
        changes = {}
        if name is not None:
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
                raise HTTPException(422, "候选素材名称需为 1–255 字符")
            changes["name"] = name.strip()
        if category is not None:
            if category not in CANDIDATE_CATEGORIES:
                raise HTTPException(422, "候选素材分类不正确")
            changes["category"] = category
        if status is not None:
            changes["status"] = self._candidate_status(status)
        if not changes:
            raise HTTPException(422, "至少提供一个候选素材修改字段")
        changes["updated_at"] = now()
        changes["version"] = version + 1
        with self.connect(write=True) as c:
            before = self._candidate_row(c, candidate_id)
            if before["version"] != version:
                raise HTTPException(
                    409, {"code": "VERSION_CONFLICT", "message": "内容已更新，请重新读取后再修改", "current": before}
                )
            if "name" in changes:
                folder_path = before["folder_path"] or self._folder_path_from_relative(before["relative_path"])
                changes["relative_path"] = f"{folder_path}/{changes['name']}" if folder_path else changes["name"]
            c.execute(
                "UPDATE candidates SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?",
                (*changes.values(), candidate_id),
            )
            self.event(c, "update_candidate", candidate_id, {"before": before, "changes": changes})
            return self._candidate_row(c, candidate_id)

    def list_candidate_folders(self, project_id, status="all"):
        if not isinstance(project_id, str) or not project_id:
            raise HTTPException(422, "project_id 不能为空")
        if status not in {"active", "disabled", "all"}:
            raise HTTPException(422, "status 只能是 active、disabled 或 all")
        with self.connect() as c:
            self.require(c, "projects", project_id)
            rows = c.execute(
                "SELECT * FROM candidate_folders WHERE project_id=? ORDER BY parent_path,sort_order,rowid",
                (project_id,),
            ).fetchall()
            disabled_paths = self._disabled_folder_paths(c, project_id)
            result = []
            for row in rows:
                inherited = any(row["path"] != path and row["path"].startswith(path + "/") for path in disabled_paths)
                effective = row["status"] == "disabled" or inherited
                if status != "all" and effective != (status == "disabled"):
                    continue
                result.append(self._folder_record(row, disabled=effective, inherited=inherited))
            return {"folders": result, "project_id": project_id, "status": status}

    def _candidate_folder_row(self, c, folder_id):
        row = c.execute("SELECT * FROM candidate_folders WHERE id=?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "候选文件夹不存在"})
        return row

    def update_candidate_folder(self, folder_id, version, *, name=None, status=None):
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HTTPException(422, "版本号不正确")
        if name is None and status is None:
            raise HTTPException(422, "至少提供文件夹名称或状态")
        if name is not None:
            name = self._normalize_folder_name(name)
        if status is not None:
            status = self._candidate_status(status)
        with self.connect(write=True) as c:
            before_row = self._candidate_folder_row(c, folder_id)
            before_effective, before_inherited = self._folder_effective_disabled(c, before_row)
            before = self._folder_record(before_row, disabled=before_effective, inherited=before_inherited)
            if before_row["version"] != version:
                raise HTTPException(
                    409, {"code": "VERSION_CONFLICT", "message": "文件夹已被其他成员修改，请重新读取", "current": before}
                )
            old_path = before_row["path"]
            new_path = old_path
            if name is not None:
                parent = before_row["parent_path"]
                new_path = f"{parent}/{name}" if parent else name
                conflict = c.execute(
                    "SELECT id FROM candidate_folders WHERE project_id=? AND path=? AND id<>?",
                    (before_row["project_id"], new_path, folder_id),
                ).fetchone()
                if conflict:
                    raise HTTPException(409, {"code": "FOLDER_EXISTS", "message": "同级文件夹已存在", "current": before})
                descendants = c.execute(
                    "SELECT * FROM candidate_folders WHERE project_id=? AND (path=? OR substr(path, 1, length(?) + 1) = ? || '/') ORDER BY LENGTH(path) DESC",
                    (before_row["project_id"], old_path, old_path, old_path),
                ).fetchall()
                for descendant in descendants:
                    suffix = descendant["path"][len(old_path):]
                    path = new_path + suffix
                    parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
                    segment = path.rsplit("/", 1)[-1]
                    c.execute(
                        "UPDATE candidate_folders SET path=?,parent_path=?,name=?,updated_at=?,version=version+1 WHERE id=?",
                        (path, parent_path, segment, now(), descendant["id"]),
                    )
                candidates = c.execute(
                    "SELECT id,relative_path,folder_path,name FROM candidates WHERE project_id=? AND deleted_at IS NULL AND (folder_path=? OR substr(folder_path, 1, length(?) + 1) = ? || '/')",
                    (before_row["project_id"], old_path, old_path, old_path),
                ).fetchall()
                for candidate in candidates:
                    suffix = candidate["folder_path"][len(old_path):]
                    folder_path = new_path + suffix
                    relative_path = f"{folder_path}/{candidate['name']}"
                    c.execute(
                        "UPDATE candidates SET folder_path=?,relative_path=?,updated_at=?,version=version+1 WHERE id=?",
                        (folder_path, relative_path, now(), candidate["id"]),
                    )
            if status is not None:
                c.execute(
                    "UPDATE candidate_folders SET status=?,updated_at=?,version=version+1 WHERE id=?",
                    (status, now(), folder_id),
                )
            elif name is not None:
                c.execute("UPDATE candidate_folders SET updated_at=? WHERE id=?", (now(), folder_id))
            self.event(
                c,
                "update_candidate_folder",
                folder_id,
                {"before": before, "name": name, "status": status, "new_path": new_path},
            )
            row = self._candidate_folder_row(c, folder_id)
            effective, inherited = self._folder_effective_disabled(c, row)
            return self._folder_record(row, disabled=effective, inherited=inherited)

    def move_candidate(self, candidate_id, version, target_folder_path=""):
        target = self._normalize_folder_path(target_folder_path)
        with self.connect(write=True) as c:
            before = self._candidate_row(c, candidate_id)
            if before["version"] != version:
                raise HTTPException(
                    409, {"code": "VERSION_CONFLICT", "message": "候选素材已被其他成员修改，请重新读取", "current": before}
                )
            if target:
                folder = c.execute(
                    "SELECT id FROM candidate_folders WHERE project_id=? AND path=?", (before["project_id"], target)
                ).fetchone()
                if folder is None:
                    raise HTTPException(404, "目标文件夹不存在")
            old_folder = before["folder_path"] or self._folder_path_from_relative(before["relative_path"])
            if target == old_folder:
                return before
            relative_path = f"{target}/{before['name']}" if target else before["name"]
            order = self._next_candidate_order(c, before["project_id"], target)
            c.execute(
                "UPDATE candidates SET folder_path=?,relative_path=?,sort_order=?,updated_at=?,version=? WHERE id=?",
                (target, relative_path, order, now(), version + 1, candidate_id),
            )
            self._prune_empty_folders(c, before["project_id"])
            self.event(c, "move_candidate", candidate_id, {"before": before, "target_folder_path": target})
            return self._candidate_row(c, candidate_id)

    def reorder_candidate_entries(self, project_id, parent_path, entries, expected_revision=None):
        parent = self._normalize_folder_path(parent_path)
        if not isinstance(entries, list) or not entries:
            raise HTTPException(422, "排序列表不能为空")
        with self.connect(write=True) as c:
            self.require(c, "projects", project_id)
            if expected_revision is not None:
                current_revision = c.execute("SELECT COALESCE(MAX(revision),0) FROM events").fetchone()[0]
                if expected_revision != current_revision:
                    raise HTTPException(
                        409,
                        {
                            "code": "REVISION_CONFLICT",
                            "message": "项目内容已变化，请重新读取后排序",
                            "current_revision": current_revision,
                        },
                    )
            folders = c.execute(
                "SELECT id FROM candidate_folders WHERE project_id=? AND parent_path=?", (project_id, parent)
            ).fetchall()
            candidates = c.execute(
                "SELECT id FROM candidates WHERE project_id=? AND folder_path=? AND deleted_at IS NULL",
                (project_id, parent),
            ).fetchall()
            expected = {("folder", row["id"]) for row in folders} | {("candidate", row["id"]) for row in candidates}
            received = []
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or entry.get("type") not in {"folder", "candidate"}
                    or not isinstance(entry.get("id"), str)
                ):
                    raise HTTPException(422, "排序条目格式不正确")
                received.append((entry["type"], entry["id"]))
            if set(received) != expected or len(received) != len(expected):
                raise HTTPException(409, {"code": "ORDER_CONFLICT", "message": "目录内容已变化，请重新读取后排序"})
            timestamp = now()
            for index, (kind, item_id) in enumerate(received):
                if kind == "folder":
                    c.execute(
                        "UPDATE candidate_folders SET sort_order=?,updated_at=?,version=version+1 WHERE id=?",
                        (float(index), timestamp, item_id),
                    )
                else:
                    c.execute(
                        "UPDATE candidates SET sort_order=?,updated_at=? WHERE id=?",
                        (float(index), timestamp, item_id),
                    )
            self.event(
                c,
                "reorder_candidate_entries",
                project_id,
                {"parent_path": parent, "entries": [{"type": kind, "id": item_id} for kind, item_id in received]},
            )
            return {
                "project_id": project_id,
                "parent_path": parent,
                "entries": [
                    {"type": kind, "id": item_id, "sort_order": index} for index, (kind, item_id) in enumerate(received)
                ],
            }

    def highlight_candidate(self, candidate_id, seconds=60):
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 300:
            raise HTTPException(422, "高亮时间需为 1–300 秒")
        expires = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + seconds, timezone.utc).isoformat()
        with self.connect(write=True) as c:
            before = self._candidate_row(c, candidate_id)
            c.execute(
                "UPDATE candidates SET highlighted_until=?,updated_at=? WHERE id=?",
                (expires, now(), candidate_id),
            )
            self.event(c, "highlight_candidate", candidate_id, {"until": expires, "seconds": seconds})
            return self._candidate_row(c, candidate_id)

    def create_candidate_reference(
        self,
        candidate_id,
        asset_id,
        variant_id=None,
        match_status=None,
        confidence=None,
        note=None,
        source=None,
    ):
        resolved_status = match_status or "unreviewed"
        resolved_note = "" if note is None else note
        resolved_source = "mcp" if source is None else source
        if resolved_status not in CANDIDATE_MATCH_STATUSES:
            raise HTTPException(422, "match_status 不正确")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
        ):
            raise HTTPException(422, "confidence 必须是 0–1 之间的数字")
        if not isinstance(resolved_note, str) or len(resolved_note) > 1000:
            raise HTTPException(422, "note 不能超过 1000 字符")
        if not isinstance(resolved_source, str) or not resolved_source.strip() or len(resolved_source.strip()) > 50:
            raise HTTPException(422, "source 需要 1–50 字符")
        with self.connect(write=True) as c:
            candidate = self._candidate_row(c, candidate_id)
            asset = self.require(c, "assets", asset_id)
            project = self.require(c, "projects", asset["project_id"])
            if candidate["project_id"] != project["id"]:
                raise HTTPException(422, "候选素材与资产不属于同一项目")
            if variant_id:
                variant = self.require(c, "variants", variant_id)
                if variant["asset_id"] != asset_id:
                    raise HTTPException(422, "变体不属于指定资产")
            existing = c.execute(
                "SELECT * FROM candidate_references WHERE candidate_id=? AND asset_id=? "
                "AND (variant_id=? OR (variant_id IS NULL AND ? IS NULL))",
                (candidate_id, asset_id, variant_id, variant_id),
            ).fetchone()
            if existing:
                reference = dict(existing)
                if any(value is not None for value in (match_status, confidence, note, source)):
                    c.execute(
                        "UPDATE candidate_references SET match_status=?,confidence=?,note=?,source=? WHERE id=?",
                        (
                            match_status if match_status is not None else existing["match_status"],
                            confidence if confidence is not None else existing["confidence"],
                            resolved_note.strip() if note is not None else existing["note"],
                            resolved_source.strip() if source is not None else existing["source"],
                            existing["id"],
                        ),
                    )
                    reference = dict(
                        c.execute("SELECT * FROM candidate_references WHERE id=?", (existing["id"],)).fetchone()
                    )
            else:
                reference_id = uid()
                created_at = now()
                c.execute(
                    "INSERT INTO candidate_references(id,candidate_id,asset_id,variant_id,created_at,match_status,confidence,note,source) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        reference_id,
                        candidate_id,
                        asset_id,
                        variant_id,
                        created_at,
                        resolved_status,
                        confidence,
                        resolved_note.strip(),
                        resolved_source.strip(),
                    ),
                )
                reference = dict(c.execute("SELECT * FROM candidate_references WHERE id=?", (reference_id,)).fetchone())
            material = c.execute(
                "SELECT * FROM materials WHERE candidate_id=? AND asset_id=? AND deleted_at IS NULL LIMIT 1",
                (candidate_id, asset_id),
            ).fetchone()
            if material is None:
                material_id = uid()
                c.execute(
                    "INSERT INTO materials(id,variant_id,name,source,file_id,created_at,deleted_at,asset_id,candidate_id) VALUES(?,?,?,?,?,?,NULL,?,?)",
                    (material_id, None, candidate["name"], "candidate", candidate["file_id"], now(), asset_id, candidate_id),
                )
                material = c.execute(
                    "SELECT m.*,f.width,f.height,f.mime,f.size,f.sha256 FROM materials m JOIN files f ON f.id=m.file_id WHERE m.id=?",
                    (material_id,),
                ).fetchone()
            self.event(
                c,
                "create_candidate_reference",
                reference["id"],
                {
                    "candidate_id": candidate_id,
                    "asset_id": asset_id,
                    "variant_id": variant_id,
                    "material_id": material["id"],
                },
            )
            return {
                "reference": reference,
                "candidate": self.candidate(candidate),
                "material": self.material(material),
            }

    def create_candidate_from_file(
        self, project_id, file_id, filename, category="其它", relative_path=None, folder_path=None
    ):
        if category not in CANDIDATE_CATEGORIES:
            raise HTTPException(422, "候选素材分类不正确")
        if relative_path is not None and folder_path is not None:
            raise HTTPException(422, "relative_path 与 folder_path 不能同时提供")
        name, parsed_path = clean_relative_path(filename)
        if folder_path is not None:
            folder_path = self._normalize_folder_path(folder_path)
            relative = f"{folder_path}/{name}" if folder_path else name
        elif relative_path:
            clean_rel = str(relative_path).replace("\\", "/").strip().strip("/")
            parts = [p for p in clean_rel.split("/") if p not in ("", ".", "..")]
            if parts:
                if parts[-1] == name:
                    folder_path = "/".join(parts[:-1])
                    relative = "/".join(parts)
                else:
                    folder_path = "/".join(parts)
                    relative = f"{folder_path}/{name}"
            else:
                folder_path = ""
                relative = name
        else:
            folder_path = self._folder_path_from_relative(parsed_path)
            relative = parsed_path

        if not isinstance(relative, str) or len(relative) > 1000:
            raise HTTPException(422, "候选素材路径过长")
        with self.connect(write=True) as c:
            self.require(c, "projects", project_id)
            self.require(c, "files", file_id)
            self._ensure_folder_records(c, project_id, folder_path)
            candidate_id = uid()
            timestamp = now()
            c.execute(
                "INSERT INTO candidates(id,project_id,file_id,name,relative_path,folder_path,sort_order,category,status,highlighted_until,created_at,updated_at,deleted_at,delete_batch,version) VALUES(?,?,?,?,?,?,?,?,'active',NULL,?,?,NULL,NULL,1)",
                (
                    candidate_id,
                    project_id,
                    file_id,
                    name,
                    relative,
                    folder_path,
                    self._next_candidate_order(c, project_id, folder_path),
                    category,
                    timestamp,
                    timestamp,
                ),
            )
            row = c.execute(
                "SELECT ca.*,f.mime,f.size,f.width,f.height,f.sha256 FROM candidates ca JOIN files f ON f.id=ca.file_id WHERE ca.id=?",
                (candidate_id,),
            ).fetchone()
            self.event(c, "register_candidate", candidate_id, {"project_id": project_id, "file_id": file_id})
            item = self.candidate(row)
            item["references"] = []
            return item

    def delete_candidate_reference(self, candidate_id, asset_id, variant_id=None):
        with self.connect(write=True) as c:
            self._candidate_row(c, candidate_id)
            existing = c.execute(
                "SELECT * FROM candidate_references WHERE candidate_id=? AND asset_id=? "
                "AND (variant_id=? OR (variant_id IS NULL AND ? IS NULL))",
                (candidate_id, asset_id, variant_id, variant_id),
            ).fetchone()
            if existing:
                c.execute("DELETE FROM candidate_references WHERE id=?", (existing["id"],))
                self.event(c, "delete_candidate_reference", existing["id"], dict(existing))
            return {"ok": True}

    def add_candidate_uploads(self, project_id, uploads, category="其它", idempotency_key=None):
        self.ensure_writable()
        if not isinstance(category, str) or category not in CANDIDATE_CATEGORIES:
            raise HTTPException(422, "候选素材分类不正确")
        if not isinstance(uploads, list) or not uploads or len(uploads) > 1000:
            raise HTTPException(422, "每次需要上传 1–1000 个候选素材")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise HTTPException(422, "请求标识需为 1–200 字符")
        temp_files = []
        staged = []
        created_paths = []
        committed = False
        try:
            with self.connect() as c:
                self.require(c, "projects", project_id)
            for item in uploads:
                if not isinstance(item, (tuple, list)) or len(item) != 3:
                    raise HTTPException(422, "上传条目格式不正确")
                filename, stream, declared_mime = item
                name, relative = clean_relative_path(filename)
                temporary = self.root / "staging" / (uid() + ".candidate")
                temp_files.append(temporary)
                total = 0
                with temporary.open("wb") as output:
                    while True:
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if not total:
                    raise HTTPException(422, f"{name} 不能是空文件")
                staged.append((temporary, name, relative, declared_mime))

            prepared = []
            for temporary, name, relative, declared_mime in staged:
                digest = hashlib.sha256()
                with temporary.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                thumb = self.root / "staging" / (uid() + ".jpg")
                temp_files.append(thumb)
                try:
                    media = inspect_media(temporary, name, declared_mime, thumb)
                except ValueError as exc:
                    raise HTTPException(422, f"{name} 不是支持的图片或视频文件") from exc
                prepared.append((temporary, thumb, name, relative, digest.hexdigest(), media))

            request_key = f"candidate-upload:{idempotency_key}" if idempotency_key else None
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "project_id": project_id,
                        "category": category,
                        "files": [
                            {
                                "name": name,
                                "relative_path": relative,
                                "sha256": digest,
                                "media": media,
                            }
                            for _, _, name, relative, digest, media in prepared
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()

            results = []
            with self.connect(write=True) as c:
                self.require(c, "projects", project_id)
                if request_key:
                    old = c.execute("SELECT * FROM requests WHERE key=?", (request_key,)).fetchone()
                    if old:
                        if old["fingerprint"] != fingerprint:
                            raise HTTPException(409, "该请求标识已用于其他内容")
                        return json.loads(old["response"])
                next_orders = {}
                for temporary, thumb, name, relative, digest, media in prepared:
                    existing = c.execute("SELECT * FROM files WHERE sha256=?", (digest,)).fetchone()
                    if existing:
                        file_id = existing["id"]
                        thumb.unlink(missing_ok=True)
                        temporary.unlink(missing_ok=True)
                    else:
                        file_id = uid()
                        original = f"originals/{file_id}.{media['ext']}"
                        thumbnail = f"thumbnails/{file_id}.jpg"
                        destination = self.root / original
                        temporary.replace(destination)
                        created_paths.append(destination)
                        thumbnail_path = self.root / thumbnail
                        thumb.replace(thumbnail_path)
                        created_paths.append(thumbnail_path)
                        c.execute(
                            "INSERT INTO files VALUES(?,?,?,?,?,?,?,?)",
                            (
                                file_id,
                                digest,
                                original,
                                thumbnail,
                                media["mime"],
                                destination.stat().st_size,
                                media["width"],
                                media["height"],
                            ),
                        )
                    candidate_id = uid()
                    timestamp = now()
                    folder_path = self._folder_path_from_relative(relative)
                    self._ensure_folder_records(c, project_id, folder_path, next_orders)
                    c.execute(
                        "INSERT INTO candidates(id,project_id,file_id,name,relative_path,folder_path,sort_order,category,status,highlighted_until,created_at,updated_at,deleted_at,delete_batch,version) VALUES(?,?,?,?,?,?,?,?,'active',NULL,?,?,NULL,NULL,1)",
                        (
                            candidate_id,
                            project_id,
                            file_id,
                            name,
                            relative,
                            folder_path,
                            self._allocate_candidate_sort_order(c, project_id, folder_path, next_orders),
                            category,
                            timestamp,
                            timestamp,
                        ),
                    )
                    row = c.execute(
                        "SELECT ca.*,f.mime,f.size,f.width,f.height,f.sha256 FROM candidates ca JOIN files f ON f.id=ca.file_id WHERE ca.id=?",
                        (candidate_id,),
                    ).fetchone()
                    item = self.candidate(row)
                    item["references"] = []
                    results.append(item)
                response = {"candidates": results, "project_id": project_id}
                self.event(c, "upload_candidates", project_id, {"candidate_ids": [item["id"] for item in results]})
                if request_key:
                    c.execute(
                        "INSERT INTO requests(key,fingerprint,response) VALUES(?,?,?)",
                        (request_key, fingerprint, json.dumps(response, ensure_ascii=False)),
                    )
            committed = True
            return response
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(507, "候选素材存储失败，请检查磁盘空间") from exc
        finally:
            for p in temp_files:
                p.unlink(missing_ok=True)
            for item in staged:
                item[0].unlink(missing_ok=True)
            for item in locals().get("prepared", []):
                item[1].unlink(missing_ok=True)
            if not committed:
                for path in created_paths:
                    Path(path).unlink(missing_ok=True)
