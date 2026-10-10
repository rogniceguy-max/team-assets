"""Atomic, retry-safe metadata intake for validated files; never transfers image bytes."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from episodes import episode_fields, episode_usage_fields


CANDIDATE_MATCH_STATUSES = {"unreviewed", "folder", "web", "exact", "approximate", "partial", "missing"}


class IntakeService:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _chunks(values, size=500):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def commit_intake(self, project_id, variants, idempotency_key, asset_id=None, asset=None):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise HTTPException(422, {"code": "INVALID_REQUEST_KEY", "message": "需要 1–200 字符的请求标识"})
        if bool(asset_id) == (asset is not None):
            raise HTTPException(422, "asset_id 与 asset 必须且只能提供一个")
        if not isinstance(variants, list) or not 1 <= len(variants) <= 100:
            raise HTTPException(422, "一次提交需要 1–100 个变体条目")
        if asset is not None:
            if not isinstance(asset, dict) or set(asset) - {"name", "category", "role"} or "name" not in asset:
                raise HTTPException(422, "新资产必须提供名称，且只能包含 name/category/role")
            asset = {"category": "人物", "role": "", **asset}
            self.service.validate_fields(asset)
        normalized = []
        for item in variants:
            if not isinstance(item, dict) or set(item) - {"variant_id", "name", "episodes", "description", "episode_usages", "file_ids", "material_names"}:
                raise HTTPException(422, "变体条目包含不支持的字段")
            existing = item.get("variant_id")
            if existing:
                if any(k in item for k in ("name", "episodes", "description", "episode_usages")):
                    raise HTTPException(422, "现有变体仅追加素材，修改字段请使用 update_variant")
            elif "name" not in item:
                raise HTTPException(422, "新变体需要名称")
            self.service.validate_fields({k: v for k, v in item.items() if k in {"name", "episodes", "description"}})
            usage_fields = episode_usage_fields(item.get("episode_usages"))
            ids, names = item.get("file_ids", []), item.get("material_names", {})
            if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) or not i for i in ids):
                raise HTTPException(422, "每个变体需要有效的 file_ids 列表，最多100项")
            if len(ids) != len(set(ids)):
                raise HTTPException(422, "同一变体条目的 file_ids 不应重复")
            if not isinstance(names, dict) or set(names) - set(ids):
                raise HTTPException(422, "material_names 必须对应本条目的 file_ids")
            if any(not isinstance(n, str) or not n.strip() or len(n) > 255 for n in names.values()):
                raise HTTPException(422, "素材名称需要 1–255 字符")
            normalized.append({**item, "episode_usages": json.loads(usage_fields["episode_usages"]), "file_ids": ids, "material_names": names})
        body = {"project_id": project_id, "asset_id": asset_id, "asset": asset, "variants": normalized}
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = "intake:" + idempotency_key
        s = self.service
        with s.connect(write=True) as c:
            old = c.execute("SELECT fingerprint,response FROM requests WHERE key=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_CONFLICT", "message": "同一请求标识已提交不同内容"})
                return json.loads(old["response"])
            s.require(c, "projects", project_id)
            if asset_id:
                selected = s.require(c, "assets", asset_id)
                if selected["project_id"] != project_id:
                    raise HTTPException(422, "资产不属于指定项目")
            else:
                asset_id = uuid.uuid4().hex
                c.execute("INSERT INTO assets(id,project_id,name,category,role) VALUES(?,?,?,?,?)",
                          (asset_id, project_id, asset["name"].strip(), asset["category"].strip(), asset["role"]))
                selected = s.require(c, "assets", asset_id)
            output_variants, output_materials = [], []
            seen_variants = set()
            for item in normalized:
                variant_id = item.get("variant_id")
                if variant_id:
                    v = s.require(c, "variants", variant_id)
                    if v["asset_id"] != asset_id:
                        raise HTTPException(422, "变体不属于指定资产")
                    if variant_id in seen_variants:
                        raise HTTPException(422, "同一批次不能重复指定一个已有变体")
                else:
                    variant_id = uuid.uuid4().hex
                    fields = episode_fields(item.get("episodes", ""))
                    c.execute("INSERT INTO variants(id,asset_id,name,episodes,description,episode_scope,episode_usages) VALUES(?,?,?,?,?,?,?)",
                              (variant_id, asset_id, item["name"].strip(), fields["episodes"], item.get("description", ""), fields["episode_scope"], json.dumps(item.get("episode_usages", []), ensure_ascii=False)))
                    v = s.require(c, "variants", variant_id)
                seen_variants.add(variant_id)
                output_variants.append(v)
                for file_id in item["file_ids"]:
                    file = s.require(c, "files", file_id)
                    mid = uuid.uuid4().hex
                    filename = item["material_names"].get(file_id, "素材." + file["path"].rsplit(".", 1)[-1])
                    filename = filename.replace("\\", "/").split("/")[-1].replace("\r", "").replace("\n", "") or "素材"
                    c.execute("INSERT INTO materials(id,variant_id,name,source,file_id,created_at,asset_id) VALUES(?,?,?,?,?,?,?)",
                              (mid, variant_id, filename, "ai", file_id, datetime.now(timezone.utc).isoformat(), asset_id))
                    output_materials.append(s.material(s.require(c, "materials", mid)))
            result = {"asset": selected, "variants": output_variants, "materials": output_materials}
            s.event(c, "commit_intake", asset_id, {"variant_ids": [v["id"] for v in output_variants], "material_ids": [m["id"] for m in output_materials]})
            c.execute("INSERT INTO requests(key,fingerprint,response) VALUES(?,?,?)", (key, fingerprint, json.dumps(result, ensure_ascii=False)))
            return result

    # ------------------------------------------------------------------
    # High-level project manifest import.  This is intentionally separate
    # from commit_intake: the latter remains the small, one-asset primitive
    # used by existing clients, while these methods make Markdown/JSON
    # imports safe to preview and apply in one transaction.
    # ------------------------------------------------------------------
    @staticmethod
    def _basename(path):
        return str(path or "素材").replace("\\", "/").split("/")[-1] or "素材"

    def _normalize_project_manifest(self, project_id, assets, *, on_name_conflict="error"):
        s = self.service
        errors, warnings, normalized = [], [], []
        if not isinstance(project_id, str) or not project_id.strip():
            errors.append({"code": "INVALID_PROJECT_ID", "message": "project_id 不能为空"})
        if on_name_conflict not in {"error", "reuse"}:
            errors.append({"code": "INVALID_CONFLICT_POLICY", "message": "on_name_conflict 只能是 error 或 reuse"})
        if not isinstance(assets, list) or not 1 <= len(assets) <= 200:
            errors.append({"code": "INVALID_ASSETS", "message": "assets 需要 1–200 个资产条目"})
            return None, errors, warnings
        try:
            with s.connect() as c:
                project = s.require(c, "projects", project_id)
                seen_names = set()
                reference_checks = []
                for asset_index, item in enumerate(assets, 1):
                    if not isinstance(item, dict) or set(item) - {"asset_id", "name", "category", "role", "variants"}:
                        errors.append({"index": asset_index, "code": "INVALID_ASSET_FIELDS", "message": "资产字段不正确"})
                        continue
                    asset_id = item.get("asset_id")
                    name = item.get("name")
                    variants = item.get("variants")
                    if asset_id:
                        row = c.execute("SELECT * FROM assets WHERE id=? AND deleted_at IS NULL", (asset_id,)).fetchone()
                        if row is None:
                            errors.append({"index": asset_index, "code": "ASSET_NOT_FOUND", "message": "资产不存在或已删除", "asset_id": asset_id})
                        elif row["project_id"] != project_id:
                            errors.append({"index": asset_index, "code": "ASSET_WRONG_PROJECT", "message": "资产不属于指定项目", "asset_id": asset_id})
                        if name is not None or item.get("category") is not None or item.get("role") is not None:
                            errors.append({"index": asset_index, "code": "EXISTING_ASSET_METADATA", "message": "已有 asset_id 时不能在导入中修改资产文字字段"})
                    else:
                        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
                            errors.append({"index": asset_index, "code": "INVALID_ASSET_NAME", "message": "新资产需要 1–200 字符名称"})
                        else:
                            clean_name = name.strip()
                            key = clean_name.casefold()
                            if key in seen_names:
                                errors.append({"index": asset_index, "code": "DUPLICATE_ASSET_NAME", "message": "导入批次内资产名称重复", "name": clean_name})
                            seen_names.add(key)
                            existing = c.execute("SELECT id FROM assets WHERE project_id=? AND name=? AND deleted_at IS NULL", (project_id, clean_name)).fetchone()
                            if existing:
                                if on_name_conflict == "reuse":
                                    asset_id = existing["id"]
                                    warnings.append({"index": asset_index, "code": "ASSET_REUSED", "message": "同名资产将复用", "asset_id": asset_id, "name": clean_name})
                                else:
                                    errors.append({"index": asset_index, "code": "ASSET_NAME_EXISTS", "message": "项目中已有同名资产，请提供 asset_id 或选择 reuse", "name": clean_name, "asset_id": existing["id"]})
                            item_name = clean_name
                        raw_category = item.get("category", "人物")
                        if raw_category is None:
                            raw_category = "人物"
                        if not isinstance(raw_category, str) or not raw_category.strip() or len(raw_category.strip()) > 100:
                            errors.append({"index": asset_index, "code": "INVALID_ASSET_CATEGORY", "message": "资产分类需要 1–100 字符字符串"})
                            clean_category = "人物"
                        else:
                            clean_category = raw_category.strip()

                        raw_role = item.get("role", "")
                        if raw_role is None:
                            raw_role = ""
                        if not isinstance(raw_role, str) or len(raw_role) > 1000:
                            errors.append({"index": asset_index, "code": "INVALID_ASSET_ROLE", "message": "资产定位说明 role 需为不超过 1000 字符的字符串"})
                            clean_role = ""
                        else:
                            clean_role = raw_role

                    if not isinstance(variants, list) or not 1 <= len(variants) <= 200:
                        errors.append({"index": asset_index, "code": "INVALID_VARIANTS", "message": "每个资产需要 1–200 个变体"})
                        variants = []
                    normalized_variants = []
                    for variant_index, variant in enumerate(variants, 1):
                        if not isinstance(variant, dict) or set(variant) - {"variant_id", "name", "episodes", "description", "episode_usages", "file_ids", "material_names", "candidate_ids", "candidate_metadata"}:
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_VARIANT_FIELDS", "message": "变体字段不正确"})
                            continue
                        variant_id = variant.get("variant_id")
                        vname = variant.get("name")
                        if variant_id:
                            vrow = c.execute("SELECT * FROM variants WHERE id=? AND deleted_at IS NULL", (variant_id,)).fetchone()
                            if vrow is None:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "VARIANT_NOT_FOUND", "message": "变体不存在或已删除", "variant_id": variant_id})
                            elif not asset_id:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "VARIANT_REQUIRES_ASSET_ID", "message": "已有 variant_id 时必须同时提供 asset_id", "variant_id": variant_id})
                            elif vrow["asset_id"] != asset_id:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "VARIANT_WRONG_ASSET", "message": "变体不属于指定资产", "variant_id": variant_id})
                            if any(key in variant for key in ("name", "episodes", "description", "episode_usages")):
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "EXISTING_VARIANT_METADATA", "message": "已有 variant_id 时不能在导入中修改变体文字字段"})
                        elif not isinstance(vname, str) or not vname.strip() or len(vname.strip()) > 200:
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_VARIANT_NAME", "message": "新变体需要 1–200 字符名称"})
                        file_ids = variant.get("file_ids", [])
                        candidate_ids = variant.get("candidate_ids", [])
                        material_names = variant.get("material_names", {})
                        candidate_metadata = variant.get("candidate_metadata", {})
                        if not isinstance(file_ids, list) or len(file_ids) > 100 or any(not isinstance(value, str) or not value for value in file_ids) or len(set(file_ids)) != len(file_ids):
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_FILE_IDS", "message": "file_ids 需要最多100个不重复 ID"})
                            file_ids = []
                        if not isinstance(candidate_ids, list) or len(candidate_ids) > 100 or any(not isinstance(value, str) or not value for value in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_CANDIDATE_IDS", "message": "candidate_ids 需要最多100个不重复 ID"})
                            candidate_ids = []
                        if not isinstance(candidate_metadata, dict) or set(candidate_metadata) - set(candidate_ids):
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_CANDIDATE_METADATA", "message": "candidate_metadata 必须对应 candidate_ids"})
                            candidate_metadata = {}
                        normalized_metadata = {}
                        for candidate_id, metadata in candidate_metadata.items():
                            if not isinstance(metadata, dict) or set(metadata) - {"match_status", "confidence", "note", "source"}:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_CANDIDATE_METADATA", "message": "候选匹配信息字段不正确", "candidate_id": candidate_id})
                                continue
                            status = metadata.get("match_status", "unreviewed")
                            confidence = metadata.get("confidence")
                            note = metadata.get("note", "")
                            source = metadata.get("source", "mcp")
                            if status not in CANDIDATE_MATCH_STATUSES:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_MATCH_STATUS", "message": "match_status 不正确", "candidate_id": candidate_id})
                            if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_CONFIDENCE", "message": "confidence 必须是 0–1 之间的数字", "candidate_id": candidate_id})
                            if not isinstance(note, str) or len(note) > 1000 or not isinstance(source, str) or not source.strip() or len(source.strip()) > 50:
                                errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_CANDIDATE_METADATA", "message": "note/source 长度或类型不正确", "candidate_id": candidate_id})
                            normalized_metadata[candidate_id] = {"match_status": status, "confidence": confidence, "note": note.strip() if isinstance(note, str) else "", "source": source.strip() if isinstance(source, str) else "mcp"}
                        if not isinstance(material_names, dict) or set(material_names) - set(file_ids):
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_MATERIAL_NAMES", "message": "material_names 必须对应 file_ids"})
                            material_names = {}
                        else:
                            for fid, fname in material_names.items():
                                if not isinstance(fname, str) or not fname.strip() or len(fname.strip()) > 255:
                                    errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_MATERIAL_NAME", "message": f"素材名称需要 1–255 字符: {fid}"})
                        try:
                            usage = json.loads(episode_usage_fields(variant.get("episode_usages", []))["episode_usages"])
                        except HTTPException as exc:
                            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": detail.get("code", "INVALID_EPISODE_USAGES"), "message": detail.get("message", "出场方式记录不正确")})
                            usage = []
                        try:
                            episode = episode_fields(variant.get("episodes", ""))
                        except HTTPException as exc:
                            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": detail.get("code", "INVALID_EPISODES"), "message": detail.get("message", "集数不正确")})
                            episode = episode_fields("")
                        raw_description = variant.get("description", "")
                        if raw_description is None:
                            raw_description = ""
                        if not isinstance(raw_description, str) or len(raw_description) > 20000:
                            errors.append({"asset_index": asset_index, "variant_index": variant_index, "code": "INVALID_VARIANT_DESCRIPTION", "message": "变体设定说明 description 需为不超过 20000 字符的字符串"})
                            clean_description = ""
                        else:
                            clean_description = raw_description
                        reference_error_index = len(errors)
                        reference_checks.extend(
                            ("file", file_id, asset_index, variant_index, reference_error_index)
                            for file_id in file_ids
                        )
                        reference_checks.extend(
                            ("candidate", candidate_id, asset_index, variant_index, reference_error_index)
                            for candidate_id in candidate_ids
                        )
                        normalized_variants.append({
                            "variant_id": variant_id, "name": vname.strip() if isinstance(vname, str) else vname,
                            "episodes": episode["episodes"], "episode_scope": json.loads(episode["episode_scope"]),
                            "description": clean_description, "episode_usages": usage,
                            "file_ids": file_ids, "material_names": material_names, "candidate_ids": candidate_ids,
                            "candidate_metadata": normalized_metadata,
                        })
                    normalized.append({"asset_id": asset_id, "name": item.get("name", "").strip() if isinstance(item.get("name"), str) else item.get("name"),
                                      "category": clean_category if not asset_id else None, "role": clean_role if not asset_id else None, "variants": normalized_variants})

                file_ids_to_check = list(dict.fromkeys(
                    value for kind, value, _, _, _ in reference_checks if kind == "file"
                ))
                candidate_ids_to_check = list(dict.fromkeys(
                    value for kind, value, _, _, _ in reference_checks if kind == "candidate"
                ))
                existing_file_ids = set()
                for id_chunk in self._chunks(file_ids_to_check):
                    placeholders = ",".join("?" for _ in id_chunk)
                    existing_file_ids.update(
                        row["id"] for row in c.execute(
                            f"SELECT id FROM files WHERE id IN ({placeholders})", id_chunk
                        ).fetchall()
                    )
                candidates_by_id = {}
                for id_chunk in self._chunks(candidate_ids_to_check):
                    placeholders = ",".join("?" for _ in id_chunk)
                    candidates_by_id.update({
                        row["id"]: row["project_id"] for row in c.execute(
                            f"SELECT id,project_id FROM candidates WHERE deleted_at IS NULL AND id IN ({placeholders})",
                            id_chunk,
                        ).fetchall()
                    })
                reference_errors = {}
                for kind, value, asset_index, variant_index, error_index in reference_checks:
                    if kind == "file" and value not in existing_file_ids:
                        reference_errors.setdefault(error_index, []).append({"asset_index": asset_index, "variant_index": variant_index, "code": "FILE_NOT_FOUND", "message": "file_id 不存在", "file_id": value})
                    elif kind == "candidate":
                        candidate_project = candidates_by_id.get(value)
                        if candidate_project is None:
                            reference_errors.setdefault(error_index, []).append({"asset_index": asset_index, "variant_index": variant_index, "code": "CANDIDATE_NOT_FOUND", "message": "候选素材不存在或已删除", "candidate_id": value})
                        elif candidate_project != project_id:
                            reference_errors.setdefault(error_index, []).append({"asset_index": asset_index, "variant_index": variant_index, "code": "CANDIDATE_WRONG_PROJECT", "message": "候选素材不属于指定项目", "candidate_id": value})
                if reference_errors:
                    ordered_errors = []
                    for error_index in range(len(errors) + 1):
                        ordered_errors.extend(reference_errors.get(error_index, ()))
                        if error_index < len(errors):
                            ordered_errors.append(errors[error_index])
                    errors = ordered_errors
                return {"project": project, "assets": normalized}, errors, warnings
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            errors.append({"code": detail.get("code", "NOT_FOUND"), "message": detail.get("message", "项目不存在")})
            return None, errors, warnings

    def plan_project_import(self, project_id, assets, *, on_name_conflict="error"):
        normalized, errors, warnings = self._normalize_project_manifest(project_id, assets, on_name_conflict=on_name_conflict)
        if normalized is None:
            return {"valid": False, "project_id": project_id, "errors": errors, "warnings": warnings, "summary": {"assets": 0, "variants": 0, "files": 0, "candidates": 0}}
        rows = normalized["assets"]
        summary = {"assets": len(rows), "variants": sum(len(item["variants"]) for item in rows),
                   "files": sum(len(v["file_ids"]) for item in rows for v in item["variants"]),
                   "candidates": sum(len(v["candidate_ids"]) for item in rows for v in item["variants"]),
                   "new_assets": sum(1 for item in rows if not item.get("asset_id"))}
        return {"valid": not errors, "project_id": project_id, "errors": errors, "warnings": warnings, "summary": summary,
                "actions": [{"asset_id": item.get("asset_id"), "name": item.get("name"), "action": "reuse" if item.get("asset_id") else "create",
                             "variant_count": len(item["variants"])} for item in rows]}

    def apply_project_import(self, project_id, assets, idempotency_key, *, on_name_conflict="error"):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise HTTPException(422, {"code": "INVALID_REQUEST_KEY", "message": "需要 1–200 字符的请求标识"})
        # Check the raw request before planning.  A retry must be able to
        # return its original response even though the newly-created names
        # would otherwise make a fresh plan report conflicts.
        raw_body = {"project_id": project_id, "assets": assets, "on_name_conflict": on_name_conflict}
        raw_fingerprint = hashlib.sha256(json.dumps(raw_body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = "project-import:" + idempotency_key
        s = self.service
        with s.connect() as c:
            old = c.execute("SELECT fingerprint,response FROM requests WHERE key=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != raw_fingerprint:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_CONFLICT", "message": "同一请求标识已提交不同导入内容"})
                return json.loads(old["response"])
        normalized, errors, warnings = self._normalize_project_manifest(project_id, assets, on_name_conflict=on_name_conflict)
        if errors:
            raise HTTPException(422, {"code": "IMPORT_PLAN_INVALID", "message": "导入计划未通过校验", "errors": errors, "warnings": warnings})
        with s.connect(write=True) as c:
            old = c.execute("SELECT fingerprint,response FROM requests WHERE key=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != raw_fingerprint:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_CONFLICT", "message": "同一请求标识已提交不同导入内容"})
                return json.loads(old["response"])
            s.require(c, "projects", project_id)
            output_assets, output_variants, output_materials, output_refs = [], [], [], []
            for item in normalized["assets"]:
                asset_id = item.get("asset_id")
                clean_name = item["name"].strip() if isinstance(item["name"], str) else str(item["name"])
                if asset_id:
                    asset_row = s.require(c, "assets", asset_id)
                else:
                    existing = c.execute("SELECT id FROM assets WHERE project_id=? AND name=? AND deleted_at IS NULL", (project_id, clean_name)).fetchone()
                    if existing:
                        if on_name_conflict == "reuse":
                            asset_id = existing["id"]
                            asset_row = s.require(c, "assets", asset_id)
                        else:
                            raise HTTPException(409, {"code": "ASSET_NAME_EXISTS", "message": f"项目内已存在同名资产：{clean_name}", "name": clean_name, "asset_id": existing["id"]})
                    else:
                        asset_id = uuid.uuid4().hex
                        c.execute("INSERT INTO assets(id,project_id,name,category,role) VALUES(?,?,?,?,?)", (asset_id, project_id, clean_name, (item.get("category") or "人物").strip(), item.get("role") or ""))
                        asset_row = s.require(c, "assets", asset_id)
                output_assets.append(asset_row)
                for spec in item["variants"]:
                    variant_id = spec.get("variant_id")
                    if variant_id:
                        variant_row = s.require(c, "variants", variant_id)
                    else:
                        variant_id = uuid.uuid4().hex
                        c.execute("INSERT INTO variants(id,asset_id,name,episodes,description,episode_scope,episode_usages) VALUES(?,?,?,?,?,?,?)",
                                  (variant_id, asset_id, spec["name"].strip(), spec["episodes"], spec["description"], json.dumps(spec["episode_scope"], ensure_ascii=False), json.dumps(spec["episode_usages"], ensure_ascii=False)))
                        variant_row = s.require(c, "variants", variant_id)
                    if variant_row["asset_id"] != asset_id:
                        raise HTTPException(422, "变体不属于指定资产")
                    output_variants.append(variant_row)
                    for file_id in spec["file_ids"]:
                        file = s.require(c, "files", file_id)
                        existing = c.execute("SELECT * FROM materials WHERE asset_id=? AND variant_id=? AND file_id=? AND deleted_at IS NULL LIMIT 1", (asset_id, variant_id, file_id)).fetchone()
                        if existing:
                            material = s.material(existing)
                        else:
                            material_id = uuid.uuid4().hex
                            filename = self._basename(spec["material_names"].get(file_id) or file["path"])
                            c.execute("INSERT INTO materials(id,variant_id,name,source,file_id,created_at,asset_id) VALUES(?,?,?,?,?,?,?)",
                                      (material_id, variant_id, filename, "ai-import", file_id, datetime.now(timezone.utc).isoformat(), asset_id))
                            material = s.material(s.require(c, "materials", material_id))
                        output_materials.append(material)
                    for candidate_id in spec["candidate_ids"]:
                        candidate = s._candidate_row(c, candidate_id)
                        if candidate["project_id"] != project_id:
                            raise HTTPException(422, "候选素材不属于指定项目")
                        existing_ref = c.execute("SELECT * FROM candidate_references WHERE candidate_id=? AND asset_id=? AND variant_id=?", (candidate_id, asset_id, variant_id)).fetchone()
                        metadata = spec["candidate_metadata"].get(candidate_id, {})
                        if existing_ref:
                            reference = dict(existing_ref)
                            if metadata:
                                c.execute("UPDATE candidate_references SET match_status=?,confidence=?,note=?,source=? WHERE id=?", (metadata.get("match_status", existing_ref["match_status"]), metadata.get("confidence", existing_ref["confidence"]), metadata.get("note", existing_ref["note"]), metadata.get("source", existing_ref["source"]), existing_ref["id"]))
                                reference = dict(c.execute("SELECT * FROM candidate_references WHERE id=?", (existing_ref["id"],)).fetchone())
                        else:
                            reference_id = uuid.uuid4().hex
                            c.execute("INSERT INTO candidate_references(id,candidate_id,asset_id,variant_id,created_at,match_status,confidence,note,source) VALUES(?,?,?,?,?,?,?,?,?)", (reference_id, candidate_id, asset_id, variant_id, datetime.now(timezone.utc).isoformat(), metadata.get("match_status", "unreviewed"), metadata.get("confidence"), metadata.get("note", ""), metadata.get("source", "mcp")))
                            reference = dict(c.execute("SELECT * FROM candidate_references WHERE id=?", (reference_id,)).fetchone())
                        material_row = c.execute("SELECT * FROM materials WHERE candidate_id=? AND asset_id=? AND deleted_at IS NULL LIMIT 1", (candidate_id, asset_id)).fetchone()
                        if material_row is None:
                            material_id = uuid.uuid4().hex
                            c.execute("INSERT INTO materials(id,variant_id,name,source,file_id,created_at,asset_id,candidate_id) VALUES(?,?,?,?,?,?,?,?)", (material_id, None, candidate["name"], "candidate", candidate["file_id"], datetime.now(timezone.utc).isoformat(), asset_id, candidate_id))
                            material_row = s.require(c, "materials", material_id)
                        output_refs.append(reference)
            result = {"project_id": project_id, "assets": output_assets, "variants": output_variants, "materials": output_materials, "references": output_refs, "warnings": warnings}
            s.event(c, "apply_project_import", project_id, {"asset_ids": [item["id"] for item in output_assets], "variant_ids": [item["id"] for item in output_variants]})
            c.execute("INSERT INTO requests(key,fingerprint,response) VALUES(?,?,?)", (key, raw_fingerprint, json.dumps(result, ensure_ascii=False)))
            return result
