import io
from contextlib import contextmanager
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from server import create_app


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(buffer, "PNG")
    return buffer.getvalue()


def test_material_file_bytes_are_staged_before_write_transaction(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        project = client.post("/api/projects", json={"name": "写锁测试"}).json()
        asset = client.post("/api/assets", json={"project_id": project["id"], "name": "资产"}).json()
        variant = client.post("/api/variants", json={"asset_id": asset["id"], "name": "状态"}).json()
        service = client.app.state.service
        connect = service.connect
        transaction_open = False
        staged_writes_inside_transaction = []

        @contextmanager
        def watched_connect(write=False):
            nonlocal transaction_open
            with connect(write=write) as connection:
                if write:
                    transaction_open = True
                try:
                    yield connection
                finally:
                    transaction_open = False

        service.connect = watched_connect
        write_bytes = Path.write_bytes

        def watched_write(path, content):
            if path.parent == service.root / "staging" and transaction_open:
                staged_writes_inside_transaction.append(path)
            return write_bytes(path, content)

        monkeypatch.setattr(Path, "write_bytes", watched_write)
        service.add_materials(
            variant["id"], [("render.png", image_bytes())], idempotency_key="staged-write-check"
        )

        assert staged_writes_inside_transaction == []
