from collections import defaultdict
from fastapi import HTTPException

from core.utils import uid
from episodes import episode_fields, episode_usage_fields, variant_record


class AssetMixin:
    """Asset and variant domain logic."""

    def _create(self, table, data, parent=None):
        self.validate_fields(data)
        if not isinstance(data.get("name"), str) or not data["name"].strip() or len(data["name"]) > 200:
            raise HTTPException(422, "名称需为 1–200 字符")
        if table == "variants":
            data.update(episode_fields(data.get("episodes", "")))
            data.update(episode_usage_fields(data.get("episode_usages")))
        data["name"] = data["name"].strip()
        data = {"id": uid(), **data}
        with self.connect(write=True) as c:
            if parent:
                self.require(c, *parent)
            c.execute(
                f"INSERT INTO {table}({','.join(data)}) VALUES({','.join('?' for _ in data)})",
                tuple(data.values()),
            )
            self.event(c, f"create_{table[:-1]}", data["id"], data)
            return self.require(c, table, data["id"])

    def create_asset(self, project_id, name, category="人物", role=""):
        return self._create(
            "assets",
            dict(project_id=project_id, name=name, category=category, role=role),
            ("projects", project_id),
        )

    def update_asset(self, asset_id, version, **changes):
        if set(changes) - {"name", "category", "role"}:
            raise HTTPException(422, "字段不正确")
        self.validate_fields(changes)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HTTPException(422, "版本号不正确")
        changes = {key: value.strip() for key, value in changes.items()}
        with self.connect(write=True) as c:
            before = self.require(c, "assets", asset_id)
            if before["version"] != version:
                raise HTTPException(
                    409, {"code": "VERSION_CONFLICT", "message": "内容已更新，请重新读取后再修改", "current": before}
                )
            if not changes:
                return before
            changes["version"] = version + 1
            c.execute(f"UPDATE assets SET {','.join(k+'=?' for k in changes)} WHERE id=?", (*changes.values(), asset_id))
            self.event(c, "update_asset", asset_id, {"before": before, "changes": changes})
            return self.require(c, "assets", asset_id)

    def search_assets(self, query="", project_id=None, category=None, limit=50, offset=0):
        self._page(limit, offset)
        filters = ["a.deleted_at IS NULL", "p.deleted_at IS NULL", "instr(lower(a.name),lower(?)) > 0"]
        values = [query]
        if project_id is not None:
            filters.append("a.project_id=?")
            values.append(project_id)
        if category is not None:
            filters.append("a.category=?")
            values.append(category)
        base = "FROM assets a JOIN projects p ON p.id=a.project_id WHERE " + " AND ".join(filters)
        with self.connect() as c:
            rows = c.execute(
                "SELECT a.*, p.name AS project_name " + base + " ORDER BY a.rowid LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            assets = []
            for r in rows:
                item = dict(r)
                pname = item.pop("project_name")
                item["path"] = f"{pname} / {item['name']}"
                assets.append(item)
            return {
                "assets": assets,
                "total": c.execute("SELECT COUNT(*) " + base, values).fetchone()[0],
                "limit": limit,
                "offset": offset,
            }

    def get_asset(self, asset_id):
        with self.connect() as c:
            result = self._path(c, "assets", self.require(c, "assets", asset_id))
            result["variants"] = [
                self._path(c, "variants", r)
                for r in c.execute(
                    "SELECT * FROM variants WHERE asset_id=? AND deleted_at IS NULL ORDER BY rowid",
                    (asset_id,),
                )
            ]
            return result

    def create_variant(self, asset_id, name, episodes="", description="", episode_usages=None):
        return self._create(
            "variants",
            dict(
                asset_id=asset_id,
                name=name,
                episodes=episodes,
                description=description,
                episode_usages=episode_usages,
            ),
            ("assets", asset_id),
        )

    def update_variant(self, variant_id, version, **changes):
        self.validate_fields(changes)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HTTPException(422, "版本号不正确")
        allowed = {"name", "episodes", "description", "episode_usages", "final_material_id"}
        if set(changes) - allowed or any(v is None for k, v in changes.items() if k != "final_material_id"):
            raise HTTPException(422, "字段不正确")
        if "name" in changes and (not changes["name"].strip() or len(changes["name"]) > 200):
            raise HTTPException(422, "名称需为 1–200 字符")
        with self.connect(write=True) as c:
            before = self.require(c, "variants", variant_id)
            if before["version"] != version:
                raise HTTPException(
                    409, {"code": "VERSION_CONFLICT", "message": "内容已更新，请重新读取后再修改", "current": before}
                )
            if "episodes" in changes and not (
                before.get("episodes_needs_review") and changes["episodes"] == before["episodes"]
            ):
                changes.update(episode_fields(changes["episodes"]))
            if "episode_usages" in changes:
                changes.update(episode_usage_fields(changes["episode_usages"]))
            if changes.get("final_material_id"):
                m = self.require(c, "materials", changes["final_material_id"])
                if m["asset_id"] != before["asset_id"] or m["deleted_at"]:
                    raise HTTPException(422, "定稿必须是同一资产中未删除的素材")
            changes["version"] = version + 1
            c.execute(
                f"UPDATE variants SET {','.join(k+'=?' for k in changes)} WHERE id=?",
                (*changes.values(), variant_id),
            )
            self.event(c, "update_variant", variant_id, {"before": before, "changes": changes})
            return self.require(c, "variants", variant_id)

    def get_variant(self, variant_id):
        with self.connect() as c:
            result = self._path(c, "variants", self.require(c, "variants", variant_id))
            result["material_count"] = c.execute(
                "SELECT COUNT(*) FROM materials WHERE asset_id=? AND deleted_at IS NULL",
                (result["asset_id"],),
            ).fetchone()[0]
            return result

    def clear_final_material(self, variant_id, version):
        return self.update_variant(variant_id, version=version, final_material_id=None)

    def audit_project_asset_coverage(self, project_id, include_disabled=False):
        """Return a read-only machine-friendly material coverage report."""
        if not isinstance(include_disabled, bool):
            raise HTTPException(422, "include_disabled 必须是布尔值")
        with self.connect() as c:
            project = self.require(c, "projects", project_id)
            disabled_paths = self._disabled_folder_paths(c, project_id)
            assets = c.execute(
                "SELECT * FROM assets WHERE project_id=? AND deleted_at IS NULL ORDER BY rowid",
                (project_id,),
            ).fetchall()

            materials_by_asset = defaultdict(list)
            for m in c.execute(
                "SELECT m.*,f.mime,f.size,f.width,f.height,f.sha256,ca.status AS candidate_status,"
                "ca.folder_path AS candidate_folder,ca.deleted_at AS candidate_deleted "
                "FROM materials m JOIN files f ON f.id=m.file_id LEFT JOIN candidates ca ON ca.id=m.candidate_id "
                "JOIN assets a ON a.id=m.asset_id "
                "WHERE a.project_id=? AND a.deleted_at IS NULL AND m.deleted_at IS NULL ORDER BY m.rowid",
                (project_id,),
            ).fetchall():
                materials_by_asset[m["asset_id"]].append(m)

            variants_by_asset = defaultdict(list)
            for v in c.execute(
                "SELECT v.* FROM variants v JOIN assets a ON a.id=v.asset_id "
                "WHERE a.project_id=? AND a.deleted_at IS NULL AND v.deleted_at IS NULL ORDER BY v.rowid",
                (project_id,),
            ).fetchall():
                variants_by_asset[v["asset_id"]].append(v)

            report_assets = []
            summary = {
                "assets_total": len(assets),
                "assets_without_candidates": 0,
                "variants_total": 0,
                "variants_finalized": 0,
                "variants_with_materials": 0,
                "variants_missing_materials": 0,
                "candidate_records": 0,
                "candidate_records_disabled": 0,
            }
            for asset_row in assets:
                asset = dict(asset_row)
                materials = materials_by_asset[asset["id"]]
                usable = []
                candidate_ids = set()
                disabled_candidate_ids = set()
                for row in materials:
                    candidate_id = row["candidate_id"]
                    if not candidate_id:
                        usable.append(row)
                        continue
                    if row["candidate_deleted"]:
                        continue
                    inherited = self._folder_inherited_disabled(
                        row["candidate_folder"] or "", disabled_paths
                    )
                    disabled = row["candidate_status"] == "disabled" or inherited
                    candidate_ids.add(candidate_id)
                    if disabled:
                        disabled_candidate_ids.add(candidate_id)
                    if include_disabled or not disabled:
                        usable.append(row)
                summary["candidate_records"] += len(candidate_ids)
                summary["candidate_records_disabled"] += len(disabled_candidate_ids)
                if not candidate_ids:
                    summary["assets_without_candidates"] += 1
                variants = []
                usable_ids = {row["id"] for row in usable}
                for variant_row in variants_by_asset[asset["id"]]:
                    variant = variant_record(variant_row)
                    final_id = variant.get("final_material_id")
                    final_available = bool(final_id and final_id in usable_ids)
                    material_count = len(usable)
                    status = "finalized" if final_available else ("has_materials" if material_count else "missing")
                    summary["variants_total"] += 1
                    summary[
                        {
                            "finalized": "variants_finalized",
                            "has_materials": "variants_with_materials",
                            "missing": "variants_missing_materials",
                        }[status]
                    ] += 1
                    variant.update(
                        {
                            "material_count": material_count,
                            "candidate_count": len(candidate_ids),
                            "disabled_candidate_count": len(disabled_candidate_ids),
                            "final_material_available": final_available,
                            "coverage_status": status,
                        }
                    )
                    variants.append(variant)
                asset.update(
                    {
                        "candidate_count": len(candidate_ids),
                        "disabled_candidate_count": len(disabled_candidate_ids),
                        "material_count": len(usable),
                        "variants": variants,
                        "coverage_status": "has_materials" if usable else "missing",
                        "candidate_coverage_status": "has_candidates" if candidate_ids else "missing",
                    }
                )
                report_assets.append(asset)
            summary["coverage_percent"] = round(
                (summary["variants_finalized"] / summary["variants_total"] * 100)
                if summary["variants_total"]
                else 0,
                2,
            )
            return {
                "project": project,
                "assets": report_assets,
                "summary": summary,
                "include_disabled": include_disabled,
            }

    def state(self):
        with self.connect() as c:
            result = {
                table: [
                    dict(r)
                    for r in c.execute(f"SELECT * FROM {table} WHERE deleted_at IS NULL ORDER BY rowid")
                ]
                for table in ("projects", "assets", "variants")
            }
            result["variants"] = [variant_record(r) for r in result["variants"]]
            result["materials"] = [
                self.material(r)
                for r in c.execute(
                    "SELECT m.*,f.width,f.height FROM materials m JOIN files f ON f.id=m.file_id WHERE m.deleted_at IS NULL ORDER BY m.rowid"
                )
            ]
            folder_rows = c.execute(
                "SELECT * FROM candidate_folders ORDER BY project_id,parent_path,sort_order,rowid"
            ).fetchall()
            result["candidate_folders"] = [
                self._folder_record(row, disabled=row["status"] == "disabled", inherited=False)
                for row in folder_rows
            ]
            disabled_by_project = {}
            for row in folder_rows:
                if row["status"] == "disabled":
                    disabled_by_project.setdefault(row["project_id"], []).append(row["path"])
            result["candidates"] = []
            rows = c.execute(
                "SELECT ca.*,f.mime,f.size,f.width,f.height,f.sha256 "
                "FROM candidates ca JOIN files f ON f.id=ca.file_id JOIN projects p ON p.id=ca.project_id "
                "WHERE ca.deleted_at IS NULL AND p.deleted_at IS NULL AND ca.status='active' "
                "ORDER BY ca.project_id,ca.folder_path,ca.sort_order,ca.rowid"
            ).fetchall()
            for row in rows:
                inherited = self._folder_inherited_disabled(
                    row["folder_path"], disabled_by_project.get(row["project_id"], [])
                )
                if not inherited:
                    result["candidates"].append(self.candidate(row))
            result["revision"] = c.execute("SELECT COALESCE(MAX(revision),0) FROM events").fetchone()[0]
            return result
