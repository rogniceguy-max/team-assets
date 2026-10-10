"""Material lifecycle, uploads, and shared asset material pool management."""
import hashlib
import io
import json
import uuid
import warnings
from datetime import datetime, timezone
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from core.config import FORMATS, MAX_UPLOAD
from core.utils import now, uid


class MaterialMixin:
    """Material domain operations."""

    def migrate_material_pool(self, c):
        columns = {r["name"]: r for r in c.execute("PRAGMA table_info(materials)")}
        if "asset_id" not in columns or columns["variant_id"]["notnull"]:
            c.execute("""CREATE TABLE materials_pool_migration(
                id TEXT PRIMARY KEY,variant_id TEXT REFERENCES variants(id),
                name TEXT NOT NULL,source TEXT NOT NULL,file_id TEXT NOT NULL REFERENCES files(id),
                created_at TEXT NOT NULL,deleted_at TEXT,delete_batch TEXT,
                version INTEGER NOT NULL DEFAULT 1,asset_id TEXT NOT NULL REFERENCES assets(id))""")
            owner = "COALESCE(m.asset_id,v.asset_id)" if "asset_id" in columns else "v.asset_id"
            c.execute(f"""INSERT INTO materials_pool_migration(rowid,id,variant_id,name,source,file_id,created_at,deleted_at,delete_batch,version,asset_id)
                SELECT m.rowid,m.id,m.variant_id,m.name,m.source,m.file_id,m.created_at,m.deleted_at,m.delete_batch,m.version,{owner}
                FROM materials m LEFT JOIN variants v ON v.id=m.variant_id ORDER BY m.rowid""")
            c.execute("DROP TABLE materials")
            c.execute("ALTER TABLE materials_pool_migration RENAME TO materials")
        c.execute("CREATE INDEX IF NOT EXISTS materials_asset_active ON materials(asset_id,deleted_at)")

    def add_materials(self, variant_id, uploads, source="manual", idempotency_key=None, *, asset_id=None):
        self.ensure_writable()
        if bool(variant_id) == bool(asset_id):
            raise HTTPException(422, "variant_id 与 asset_id 必须且只能提供一个")
        self.validate_fields({"source": source})
        if not uploads or len(uploads) > 20:
            raise HTTPException(422, "每次上传 1–20 个图片文件")
        if sum(len(body) for _, body in uploads) > MAX_UPLOAD:
            raise HTTPException(413, "每次上传总量不能超过 30 MB")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 200
        ):
            raise HTTPException(422, "请求标识需为 1–200 字符")

        target = variant_id if variant_id else {"asset_id": asset_id}
        fingerprint = hashlib.sha256(
            json.dumps(
                [target, source, [(n, hashlib.sha256(b).hexdigest()) for n, b in uploads]],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        prepared = []
        for filename, body in uploads:
            if len(body) > MAX_UPLOAD:
                raise HTTPException(413, "单个文件不能超过 30 MB")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(body)) as im:
                        fmt, size = im.format, im.size
                        if fmt not in FORMATS:
                            raise HTTPException(422, "只支持 PNG、JPEG、WebP 和 GIF 图片")
                        im.verify()
                    with Image.open(io.BytesIO(body)) as im:
                        im.seek(0)
                        im = ImageOps.exif_transpose(im)
                        size = im.size
                        im.thumbnail((640, 640))
                        output = io.BytesIO()
                        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                            rgba = im.convert("RGBA")
                            white_bg = Image.new("RGB", rgba.size, (255, 255, 255))
                            white_bg.paste(rgba, mask=rgba.split()[3])
                            white_bg.save(output, "JPEG", quality=85)
                        else:
                            im.convert("RGB").save(output, "JPEG", quality=85)
            except (
                UnidentifiedImageError,
                OSError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
                ValueError,
            ) as exc:
                raise HTTPException(422, "文件不是有效图片或像素尺寸过大") from exc
            prepared.append((filename, body, hashlib.sha256(body).hexdigest(), fmt, size, output.getvalue()))
        created_paths = []
        staging_paths = []
        try:
            digests = list(dict.fromkeys(item[2] for item in prepared))
            placeholders = ",".join("?" for _ in digests)
            with self.connect() as c:
                if variant_id:
                    asset_id = self.require(c, "variants", variant_id)["asset_id"]
                self.require(c, "assets", asset_id)
                if idempotency_key:
                    old = c.execute("SELECT * FROM requests WHERE key=?", (idempotency_key,)).fetchone()
                    if old:
                        if old["fingerprint"] != fingerprint:
                            raise HTTPException(409, "该请求标识已用于其他内容")
                        return json.loads(old["response"])
                existing_digests = {
                    row["sha256"]
                    for row in c.execute(
                        f"SELECT sha256 FROM files WHERE sha256 IN ({placeholders})", digests
                    )
                }

            staged_by_digest = {}
            for _, body, digest, fmt, size, thumb in prepared:
                if digest in existing_digests or digest in staged_by_digest:
                    continue
                file_id = uid()
                ext, mime = FORMATS[fmt]
                original = f"originals/{file_id}.{ext}"
                thumbnail = f"thumbnails/{file_id}.jpg"
                staged_original = self.root / "staging" / f"{uid()}.material"
                staged_thumbnail = self.root / "staging" / f"{uid()}.thumbnail"
                staging_paths.append(staged_original)
                staged_original.write_bytes(body)
                staging_paths.append(staged_thumbnail)
                staged_thumbnail.write_bytes(thumb)
                staged_by_digest[digest] = {
                    "file_id": file_id,
                    "original": original,
                    "thumbnail": thumbnail,
                    "mime": mime,
                    "size": len(body),
                    "dimensions": size,
                    "staged_original": staged_original,
                    "staged_thumbnail": staged_thumbnail,
                }

            with self.connect(write=True) as c:
                if variant_id:
                    asset_id = self.require(c, "variants", variant_id)["asset_id"]
                self.require(c, "assets", asset_id)
                if idempotency_key:
                    old = c.execute("SELECT * FROM requests WHERE key=?", (idempotency_key,)).fetchone()
                    if old:
                        if old["fingerprint"] != fingerprint:
                            raise HTTPException(409, "该请求标识已用于其他内容")
                        return json.loads(old["response"])
                results = []
                for filename, body, digest, fmt, size, thumb in prepared:
                    f = c.execute("SELECT * FROM files WHERE sha256=?", (digest,)).fetchone()
                    if f:
                        file_id = f["id"]
                    else:
                        staged = staged_by_digest.get(digest)
                        if staged is None:
                            raise RuntimeError("文件记录在上传准备期间发生变化，请重试")
                        file_id = staged["file_id"]
                        original = staged["original"]
                        thumbnail = staged["thumbnail"]
                        for key, relative in (
                            ("staged_original", original),
                            ("staged_thumbnail", thumbnail),
                        ):
                            destination = self.root / relative
                            (staged[key]).replace(destination)
                            created_paths.append(destination)
                        c.execute(
                            "INSERT INTO files VALUES(?,?,?,?,?,?,?,?)",
                            (
                                file_id,
                                digest,
                                original,
                                thumbnail,
                                staged["mime"],
                                staged["size"],
                                *staged["dimensions"],
                            ),
                        )
                    mid = uid()
                    clean_name = filename.replace("\\", "/").split("/")[-1][:255] or "图片"
                    c.execute(
                        "INSERT INTO materials(id,variant_id,name,source,file_id,created_at,deleted_at,asset_id) VALUES(?,?,?,?,?,?,NULL,?)",
                        (mid, variant_id, clean_name, source[:100], file_id, now(), asset_id),
                    )
                    results.append(self.material(self.require(c, "materials", mid)))
                response = {"materials": results}
                self.event(
                    c,
                    "upload_materials",
                    variant_id or asset_id,
                    {"materials": [m["id"] for m in results], "source": source, "asset_id": asset_id},
                )
                if idempotency_key:
                    c.execute(
                        "INSERT INTO requests VALUES(?,?,?)",
                        (idempotency_key, fingerprint, json.dumps(response)),
                    )
                return response
        except Exception:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            for path in staging_paths:
                path.unlink(missing_ok=True)

    def delete_material(self, material_id):
        with self.connect(write=True) as c:
            m = self.require(c, "materials", material_id, include_deleted=True)
            if not m["deleted_at"]:
                self.require(c, "assets", m["asset_id"])
                c.execute(
                    "UPDATE materials SET deleted_at=?,delete_batch=?,version=version+1 WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), uuid.uuid4().hex, material_id),
                )
                c.execute(
                    "UPDATE variants SET final_material_id=NULL,version=version+1 WHERE final_material_id=?",
                    (material_id,),
                )
                self.event(c, "delete_material", material_id, m)
            return {"ok": True}

    def restore_material(self, material_id):
        with self.connect(write=True) as c:
            m = self.require(c, "materials", material_id, include_deleted=True)
            self._ensure_restore_parent(c, "materials", m)
            if m["deleted_at"]:
                cid = m["candidate_id"] if "candidate_id" in m.keys() else None
                if cid and m["asset_id"]:
                    conflict = c.execute(
                        "SELECT id FROM materials WHERE candidate_id=? AND asset_id=? AND deleted_at IS NULL AND id!=?",
                        (cid, m["asset_id"], material_id),
                    ).fetchone()
                    if conflict:
                        raise HTTPException(
                            409,
                            {
                                "code": "CANDIDATE_ALREADY_REFERENCED",
                                "message": "该候选素材当前已被该资产引用为有效素材，无法恢复重复引用",
                                "conflict_id": conflict["id"],
                            },
                        )
                c.execute(
                    "UPDATE materials SET deleted_at=NULL,delete_batch=NULL,version=version+1 WHERE id=?",
                    (material_id,),
                )
                self.event(c, "restore_material", material_id)
            return self.material(self.require(c, "materials", material_id))

    def list_materials(self, variant_id=None, limit=50, offset=0, asset_id=None):
        self._page(limit, offset)
        with self.connect() as c:
            if bool(variant_id) == bool(asset_id):
                raise HTTPException(422, "variant_id 与 asset_id 必须且只能提供一个")
            if variant_id:
                asset_id = self.require(c, "variants", variant_id)["asset_id"]
            self.require(c, "assets", asset_id)
            rows = c.execute(
                "SELECT m.*,f.mime,f.size,f.width,f.height,f.sha256 FROM materials m JOIN files f ON f.id=m.file_id WHERE m.asset_id=? AND m.deleted_at IS NULL ORDER BY m.rowid LIMIT ? OFFSET ?",
                (asset_id, limit, offset),
            )
            return {
                "materials": [self._path(c, "materials", self.material(r)) for r in rows],
                "total": c.execute(
                    "SELECT COUNT(*) FROM materials WHERE asset_id=? AND deleted_at IS NULL", (asset_id,)
                ).fetchone()[0],
                "limit": limit,
                "offset": offset,
            }
