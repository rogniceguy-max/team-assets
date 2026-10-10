import sqlite3
from contextlib import contextmanager
import pytest
from server import AssetService

class QueryCounter:
    def __init__(self, service):
        self.service = service
        self.count = 0
        self.queries = []
        self._orig_connect = service.connect

    def __enter__(self):
        @contextmanager
        def counted_connect(*args, **kwargs):
            with self._orig_connect(*args, **kwargs) as c:
                def trace(sql):
                    s = sql.strip()
                    if s.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
                        self.count += 1
                        self.queries.append(s)
                c.set_trace_callback(trace)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)
        self.service.connect = counted_connect
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.service.connect = self._orig_connect

def test_list_candidates_no_n_plus_one(tmp_path):
    service = AssetService(tmp_path)
    proj = service.create_project("Test N+1 Candidates")
    pid = proj["id"]

    asset = service.create_asset(pid, "Asset 1")
    variant = service.create_variant(asset["id"], "Variant 1")

    # Insert 20 candidates, a file, and references
    with service.connect(write=True) as c:
        c.execute("INSERT INTO files VALUES('f1', 'sha1', 'originals/f1.png', 'thumbnails/f1.jpg', 'image/png', 100, 10, 10)")
        for i in range(20):
            cid = f"cand_{i:02d}"
            c.execute(
                "INSERT INTO candidates(id, project_id, file_id, name, relative_path, folder_path, category, status, sort_order, created_at, updated_at, version) "
                "VALUES(?, ?, 'f1', ?, ?, '', '其它', 'active', ?, '2026-01-01', '2026-01-01', 1)",
                (cid, pid, f"Cand_{i:02d}.png", f"Cand_{i:02d}.png", float(i))
            )
            if i < 5:
                c.execute(
                    "INSERT INTO candidate_references(id, candidate_id, asset_id, variant_id, created_at, match_status, confidence, note, source) "
                    "VALUES(?, ?, ?, ?, '2026-01-01', 'exact', 1.0, '', 'manual')",
                    (f"cr_{i}", cid, asset["id"], variant["id"])
                )

    # Test query count during list_candidates
    with QueryCounter(service) as qc:
        result = service.list_candidates(pid, limit=20)

    # In the old code, this was 1 (projects check) + 1 (folder paths) + 1 (select candidates) + 20 (references query per candidate) = 23 queries!
    # With batching, it must be <= 5 queries total!
    assert qc.count <= 5, f"Expected <= 5 queries, got {qc.count}: {qc.queries}"
    assert len(result["candidates"]) == 20
    for item in result["candidates"][:5]:
        assert len(item["references"]) >= 1
        assert item["references"][0]["asset_id"] == asset["id"]
    for item in result["candidates"][5:]:
        assert len(item["references"]) == 0

def test_search_assets_no_n_plus_one(tmp_path):
    service = AssetService(tmp_path)
    proj = service.create_project("Search Project")

    for i in range(20):
        service.create_asset(proj["id"], f"Asset_{i:02d}")

    with QueryCounter(service) as qc:
        result = service.search_assets(project_id=proj["id"], limit=20)

    # 1 for select assets with joined project name, 1 for count = 2 queries!
    # In old code: 1 select + 1 count + 20 project lookups = 22 queries.
    assert qc.count == 2, f"Expected exactly 2 queries, got {qc.count}: {qc.queries}"
    assert len(result["assets"]) == 20
    for a in result["assets"]:
        assert a["path"] == f"Search Project / {a['name']}"

def test_audit_project_asset_coverage_no_n_plus_one(tmp_path):
    service = AssetService(tmp_path)
    proj = service.create_project("Audit Project")

    # Create 10 assets, each with a variant
    for i in range(10):
        asset = service.create_asset(proj["id"], f"A_{i}")
        variant = service.create_variant(asset["id"], f"V_{i}")

    with QueryCounter(service) as qc:
        audit = service.audit_project_asset_coverage(proj["id"])

    # In old code: 1 (require) + 1 (disabled folders) + 1 (select assets) + 10*2 (materials + variants per asset) = 23 queries!
    # In optimized code: 1 + 1 + 1 + 1 (all materials) + 1 (all variants) = 5 queries!
    assert qc.count <= 6, f"Expected <= 6 queries, got {qc.count}: {qc.queries}"
    assert audit["summary"]["assets_total"] == 10
    assert audit["summary"]["variants_total"] == 10
