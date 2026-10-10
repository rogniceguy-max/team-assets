import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import AssetService, create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def variant(client):
    p = client.get('/api/state').json()['projects'][0]['id']
    a = client.post('/api/assets', json={'project_id': p, 'name': '人物', 'category': '人物'}).json()
    return client.post('/api/variants', json={'asset_id': a['id'], 'name': '战衣'}).json()


def png(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (40, 30), color).save(output, 'PNG')
    return output.getvalue()


def test_upload_retry_final_delete_restore_and_persistence(client):
    v = variant(client)
    url = f"/api/variants/{v['id']}/materials"
    files = [('files', ('图.png', png(), 'image/png'))]
    r = client.post(url, files=files, headers={'Idempotency-Key': 'request-1'})
    assert r.status_code == 201
    material = r.json()['materials'][0]
    assert client.post(url, files=files, headers={'Idempotency-Key': 'request-1'}).json() == r.json()
    assert len(client.get('/api/state').json()['materials']) == 1
    assert client.get(material['url']).content == png()
    assert client.get(material['thumbnail_url']).headers['content-type'] == 'image/jpeg'
    patch = client.patch(f"/api/variants/{v['id']}", json={'version': 1, 'final_material_id': material['id']})
    assert patch.status_code == 200
    assert client.patch(f"/api/variants/{v['id']}", json={'version': 1, 'name': '覆盖'}).status_code == 409
    assert client.delete(f"/api/materials/{material['id']}").status_code == 200
    state = client.get('/api/state').json()
    assert not state['materials']
    assert state['variants'][0]['final_material_id'] is None
    assert state['variants'][0]['version'] == 3
    assert len(client.get('/api/trash').json()['materials']) == 1
    client.post(f"/api/materials/{material['id']}/restore")
    assert len(AssetService(client.app.state.service.root).state()['materials']) == 1


def test_invalid_batch_is_atomic_and_idempotency_conflict(client):
    v = variant(client)
    url = f"/api/variants/{v['id']}/materials"
    r = client.post(url, files=[('files', ('ok.png', png())), ('files', ('bad.png', b'not an image'))])
    assert r.status_code == 422
    assert client.get('/api/state').json()['materials'] == []
    assert list((client.app.state.service.root / 'originals').iterdir()) == []
    assert client.post(url, files={'files': ('ok.png', png())}, headers={'Idempotency-Key': 'key'}).status_code == 201
    assert client.post(url, files={'files': ('different.png', png('blue'))}, headers={'Idempotency-Key': 'key'}).status_code == 409


def test_foreign_final_and_image_dedup(client):
    v1, v2 = variant(client), variant(client)
    materials = []
    for v in (v1, v2):
        materials.append(client.post(f"/api/variants/{v['id']}/materials", files={'files': ('same.png', png())}).json()['materials'][0])
    assert materials[0]['file_id'] == materials[1]['file_id']
    assert client.patch(f"/api/variants/{v1['id']}", json={'version': 1, 'final_material_id': materials[1]['id']}).status_code == 422
    assert client.post('/api/assets', json={'project_id': 'missing', 'name': 'x'}).status_code == 404
    assert client.post('/api/projects', json={'name': '  '}).status_code == 422


def test_independent_service_connections_serialize_updates_and_retries(client):
    v = variant(client)
    services = [AssetService(client.app.state.service.root) for _ in range(2)]

    def patch(service):
        try:
            service.update_variant(v['id'], 1, name='并发修改')
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(patch, services)) == [200, 409]
        results = list(pool.map(lambda s: s.add_materials(v['id'], [('x.png', png())], idempotency_key='parallel'), services))
    assert results[0] == results[1]
    assert len(client.get('/api/state').json()['materials']) == 1


def test_cross_origin_writes_and_download(client):
    assert client.post('/api/projects', json={'name': '恶意页面'}, headers={'Origin': 'https://other.test'}).status_code == 403
    assert client.post('/api/projects', json={'name': '项目'}, headers={'Origin': 'http://testserver'}).status_code == 201
    v = variant(client)
    m = client.post(f"/api/variants/{v['id']}/materials", files={'files': ('原图.png', png())}).json()['materials'][0]
    assert client.get(m['url'] + '?download=true').headers['content-disposition'].startswith('attachment;')
    with pytest.raises(HTTPException) as error:
        client.app.state.service.create_variant(v['asset_id'], 'x', description='x' * 20001)
    assert error.value.status_code == 422


def test_asset_edit_preserves_children_conflicts_and_history(client):
    v = variant(client)
    client.post(f"/api/variants/{v['id']}/materials", files={'files': ('image.png', png())})
    before = client.get('/api/state').json()
    asset = before['assets'][0]
    url = f"/api/assets/{asset['id']}"
    response = client.patch(url, json={'version': asset['version'], 'name': ' 新名称 ', 'category': '道具', 'role': '关键线索'})
    assert response.status_code == 200
    assert response.json() == {**asset, 'name': '新名称', 'category': '道具', 'role': '关键线索', 'version': 2}
    assert client.patch(url, json={'version': 1, 'name': '旧修改'}).status_code == 409
    after = client.get('/api/state').json()
    assert after['variants'] == before['variants']
    assert after['materials'] == before['materials']
    assert after['revision'] == before['revision'] + 1
    event = client.get('/api/history').json()['events'][0]
    assert event['action'] == 'update_asset'
    assert event['details']['before'] == asset
    assert client.patch(url, json={'version': 2, 'role': ''}).json()['role'] == ''
    for changes in ({'name': '  '}, {'category': '  '}, {'name': None}, {'role': None}, {'project_id': 'other'}):
        assert client.patch(url, json={'version': 3, **changes}).status_code == 422
    for field in ('name', 'category'):
        with pytest.raises(HTTPException) as error:
            client.app.state.service.update_asset(asset['id'], 3, **{field: ' '})
        assert error.value.status_code == 422


def test_legacy_asset_schema_migration_preserves_data(tmp_path):
    database = tmp_path / 'database'
    database.mkdir()
    with sqlite3.connect(database / 'assets.sqlite3') as c:
        c.executescript("""
            CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '');
            CREATE TABLE assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,category TEXT NOT NULL,role TEXT NOT NULL DEFAULT '');
            INSERT INTO projects VALUES('p','旧项目','保留说明');
            INSERT INTO assets VALUES('a','p','旧资产','人物','主角');
        """)
    service = AssetService(tmp_path)
    assert service.state()['assets'] == [{'id': 'a', 'project_id': 'p', 'name': '旧资产', 'category': '人物', 'role': '主角', 'version': 1, 'deleted_at': None, 'delete_batch': None}]
    service.update_asset('a', 1, name='新资产')
    reopened = AssetService(tmp_path).state()
    assert reopened['assets'][0]['version'] == 2
    assert reopened['assets'][0]['name'] == '新资产'
    assert reopened['projects'][0]['description'] == '保留说明'
