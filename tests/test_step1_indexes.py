import tempfile
import shutil
from server import AssetService

def test_step_1_indexes():
    tmp = tempfile.mkdtemp()
    try:
        s = AssetService(tmp)
        with s.connect() as c:
            indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
            assert "assets_project_active" in indexes, "missing assets_project_active"
            assert "variants_asset_active" in indexes, "missing variants_asset_active"
            assert "materials_variant_active" in indexes, "missing materials_variant_active"
            assert "materials_asset_active" in indexes, "missing materials_asset_active"

            plan = c.execute("EXPLAIN QUERY PLAN SELECT * FROM assets WHERE project_id=? AND deleted_at IS NULL", ("p1",)).fetchall()
            assert any("assets_project_active" in r[-1] for r in plan)

            plan_v = c.execute("EXPLAIN QUERY PLAN SELECT * FROM variants WHERE asset_id=? AND deleted_at IS NULL", ("a1",)).fetchall()
            assert any("variants_asset_active" in r[-1] for r in plan_v)

            plan_m = c.execute("EXPLAIN QUERY PLAN SELECT * FROM materials WHERE variant_id=? AND deleted_at IS NULL", ("v1",)).fetchall()
            assert any("materials_variant_active" in r[-1] for r in plan_m)

            v_cols = {r[1] for r in c.execute("PRAGMA table_info(variants)").fetchall()}
            assert "episode_scope" in v_cols
            assert "episodes_original" in v_cols
            assert "episodes_needs_review" in v_cols
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
