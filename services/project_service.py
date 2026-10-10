"""Project lifecycle, migration, and cascade soft-deletion/restoration."""
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException

from core.config import TABLES, PARENTS
from episodes import variant_record


class ProjectMixin:
    """Project domain operations, migrations, and unified entity deletion/restoration."""
    def migrate_domain(self, c, fresh_store=False):
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for table in ("projects", "assets", "variants", "materials"):
            columns = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, definition in (
                ("deleted_at", "TEXT"),
                ("delete_batch", "TEXT"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        if (
            fresh_store
            and not c.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone()
            and not c.execute("SELECT 1 FROM projects LIMIT 1").fetchone()
        ):
            c.execute(
                "INSERT INTO projects(id,name,description) VALUES(?,?,?)",
                (uuid.uuid4().hex, "团队项目", "团队共享的资产空间"),
            )
        c.execute("INSERT OR IGNORE INTO settings VALUES('initialized','1')")
        self.migrate_material_pool(c)

    def list_projects(self, limit=50, offset=0):
        self._page(limit, offset)
        with self.connect() as c:
            return {
                "projects": [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY rowid LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ],
                "total": c.execute("SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL").fetchone()[0],
                "limit": limit,
                "offset": offset,
            }

    def create_project(self, name, description=""):
        return self._create("projects", dict(name=name, description=description))

    def update_project(self, project_id, version, **changes):
        if set(changes) - {"name", "description"}:
            raise HTTPException(422, "字段不正确")
        self.validate_fields(changes)
        with self.connect(write=True) as c:
            before = self.require(c, "projects", project_id)
            self.check_version(before, version)
            if not changes:
                return before
            changes = {k: v.strip() for k, v in changes.items()}
            changes["version"] = version + 1
            c.execute(
                f"UPDATE projects SET {','.join(k+'=?' for k in changes)} WHERE id=?",
                (*changes.values(), project_id),
            )
            self.event(c, "update_project", project_id, {"before": before, "changes": changes})
            return self.require(c, "projects", project_id)

    def delete_entity(self, entity_type, item_id, version, dry_run=False):
        if entity_type not in TABLES:
            raise HTTPException(422, "类型必须是 project、asset 或 variant")
        table = TABLES[entity_type]
        with self.connect(write=True) as c:
            before = self.require(c, table, item_id)
            self.check_version(before, version)
            tree = self._subtree(c, table, item_id)
            active = {t: [r for r in rows if not r["deleted_at"]] for t, rows in tree.items()}
            counts = {t: len(rows) for t, rows in active.items()}
            if dry_run:
                return {
                    "dry_run": True,
                    "entity_type": entity_type,
                    "id": item_id,
                    "affected": counts,
                    "batch_id": None,
                }
            batch = uuid.uuid4().hex
            stamp = datetime.now(timezone.utc).isoformat()
            for target, rows in active.items():
                c.executemany(
                    f"UPDATE {target} SET deleted_at=?,delete_batch=?,version=version+1 WHERE id=?",
                    [(stamp, batch, r["id"]) for r in rows],
                )
            self.event(c, f"delete_{entity_type}", item_id, {"batch_id": batch, "affected": counts})
            return {"ok": True, "batch_id": batch, "affected": counts}

    def restore_entity(self, entity_type, item_id):
        if entity_type not in TABLES:
            raise HTTPException(422, "类型必须是 project、asset 或 variant")
        table = TABLES[entity_type]
        with self.connect(write=True) as c:
            before = self.require(c, table, item_id, include_deleted=True)
            self._ensure_restore_parent(c, table, before)
            counts = {t: 0 for t in (*TABLES.values(), "materials")}
            if before["deleted_at"]:
                restored_variant_ids = []
                tree = self._subtree(c, table, item_id)
                if table == "variants" and before["delete_batch"]:
                    tree["materials"] = [
                        dict(r)
                        for r in c.execute(
                            "SELECT * FROM materials WHERE variant_id=? AND asset_id=? AND delete_batch=? AND deleted_at IS NOT NULL",
                            (item_id, before["asset_id"], before["delete_batch"]),
                        )
                    ]
                for target, rows in tree.items():
                    if target == "materials":
                        safe_ids = []
                        for r in rows:
                            if r["deleted_at"] and r["delete_batch"] == before["delete_batch"]:
                                cid = r["candidate_id"] if "candidate_id" in r.keys() else None
                                if cid and r["asset_id"]:
                                    conflict = c.execute(
                                        "SELECT id FROM materials WHERE candidate_id=? AND asset_id=? AND deleted_at IS NULL AND id!=?",
                                        (cid, r["asset_id"], r["id"]),
                                    ).fetchone()
                                    if conflict:
                                        continue
                                safe_ids.append(r["id"])
                        ids = safe_ids
                    else:
                        ids = [
                            r["id"]
                            for r in rows
                            if r["deleted_at"] and r["delete_batch"] == before["delete_batch"]
                        ]
                    if target == "variants":
                        restored_variant_ids = ids
                    counts[target] = len(ids)
                    if ids:
                        c.executemany(
                            f"UPDATE {target} SET deleted_at=NULL,delete_batch=NULL,version=version+1 WHERE id=?",
                            [(i,) for i in ids],
                        )
                cleared_final_references = 0
                for variant_id in restored_variant_ids:
                    variant = c.execute(
                        "SELECT asset_id,final_material_id FROM variants WHERE id=?", (variant_id,)
                    ).fetchone()
                    if not variant or not variant["final_material_id"]:
                        continue
                    final = c.execute(
                        "SELECT asset_id,deleted_at FROM materials WHERE id=?",
                        (variant["final_material_id"],),
                    ).fetchone()
                    if not final or final["deleted_at"] or final["asset_id"] != variant["asset_id"]:
                        c.execute(
                            "UPDATE variants SET final_material_id=NULL,version=version+1 WHERE id=?",
                            (variant_id,),
                        )
                        cleared_final_references += 1
                self.event(
                    c,
                    f"restore_{entity_type}",
                    item_id,
                    {
                        "affected": counts,
                        "batch_id": before["delete_batch"],
                        "cleared_final_references": cleared_final_references,
                    },
                )
            return {"ok": True, "affected": counts, "entity": self.require(c, table, item_id)}

    def trash(self):
        with self.connect() as c:
            result = {
                t: [
                    dict(r)
                    for r in c.execute(
                        f"SELECT * FROM {t} WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
                    )
                ]
                for t in TABLES.values()
            }
            result["variants"] = [variant_record(r) for r in result["variants"]]
            result["materials"] = [
                self.material(r)
                for r in c.execute(
                    "SELECT * FROM materials WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
                )
            ]
            return result
