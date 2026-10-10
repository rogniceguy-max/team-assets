import io
import base64
from PIL import Image
from starlette.testclient import TestClient
from mcp_server import create_app
from test_mcp import call, rpc


def ok(client, name, args):
    result = call(client, name, args)
    assert not result.get('isError'), result
    return result['structuredContent']


def test_v2_upload_atomic_intake_preview_and_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('TEAM_ASSETS_PUBLIC_MCP_URL', 'http://nas:8093')
    with TestClient(create_app(tmp_path, 'test-secret')) as client:
        names = {t['name'] for t in rpc(client, 'tools/list')['tools']}
        assert names >= {'delete_project','restore_project','delete_asset','restore_asset','delete_variant','restore_variant','commit_intake','begin_upload','clear_final_material'}
        project = ok(client, 'create_project', {'name':'新项目'})
        data = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(data, 'PNG')
        body = data.getvalue()
        upload = ok(client, 'begin_upload', {'filename':'render.png','size':len(body),'idempotency_key':'session1'})
        assert upload['upload_url'].startswith('http://nas:8093/uploads/')
        response = client.put(upload['relative_path']+'?offset=0', content=body, headers={'Authorization':'Bearer '+upload['upload_token']})
        assert response.status_code == 200, response.text
        ready = ok(client, 'complete_upload', {'upload_id':upload['id']})
        args = {'project_id':project['id'], 'asset':{'name':'角色'}, 'variants':[{'name':'战衣','file_ids':[ready['file_id']]}], 'idempotency_key':'intake1'}
        result = ok(client, 'commit_intake', args)
        assert ok(client, 'commit_intake', args) == result
        asset, variant, material = result['asset'], result['variants'][0], result['materials'][0]
        existing_args = {'project_id':project['id'], 'asset_id':asset['id'], 'variants':[{'variant_id':variant['id'],'file_ids':[ready['file_id']]}], 'idempotency_key':'existing-intake'}
        existing = ok(client, 'commit_intake', existing_args)
        assert existing['variants'][0]['id'] == variant['id']
        assert len(existing['materials']) == 1
        assert ok(client, 'commit_intake', existing_args) == existing
        rejected = call(client, 'commit_intake', {**existing_args, 'idempotency_key':'forbidden-edit', 'variants':[{'variant_id':variant['id'], 'description':'', 'file_ids':[]}]})
        assert rejected['isError']
        preview = call(client, 'get_material_preview', {'material_id':material['id']})
        assert preview['content'][0]['type'] == 'image'
        final = ok(client, 'set_final_material', {'variant_id':variant['id'],'material_id':material['id'],'version':variant['version']})
        clear = ok(client, 'clear_final_material', {'variant_id':variant['id'],'version':final['version']})
        assert clear['final_material_id'] is None
        ok(client, 'preview_deletion', {'entity_type':'asset','item_id':asset['id'],'version':asset['version']})
        ok(client, 'delete_asset', {'asset_id':asset['id'],'version':asset['version']})
        missing = call(client, 'get_asset', {'asset_id':asset['id']})
        assert missing['isError'] and missing['structuredContent']['error']['status'] == 409
        ok(client, 'restore_asset', {'asset_id':asset['id']})
        assert ok(client, 'get_asset', {'asset_id':asset['id']})
        assert ok(client, 'search_assets', {'query':'角色'})


def test_structured_validation_error_and_upload_route_auth(tmp_path):
    with TestClient(create_app(tmp_path, 'test-secret')) as client:
        result = call(client, 'upload_materials', {'variant_id':'missing','files':[],'idempotency_key':'a'})
        assert result['isError']
        assert result['structuredContent']['error']['code'] == 'validation_error'
        upload = ok(client, 'begin_upload', {'filename':'a.png','size':20})
        assert client.put(upload['relative_path'], content=b'x').status_code == 401
        assert client.put('/uploads-malicious', content=b'x').status_code == 401


def test_mcp_asset_pool_upload_shared_final_and_source_deletion(tmp_path):
    with TestClient(create_app(tmp_path, 'test-secret')) as client:
        p = ok(client, 'create_project', {'name': '共享测试'})
        a = ok(client, 'create_asset', {'project_id': p['id'], 'name': '角色'})
        body = io.BytesIO()
        Image.new('RGB', (20, 10), 'blue').save(body, 'PNG')
        m = ok(client, 'upload_materials', {'asset_id': a['id'], 'files': [{'filename': 'shared.png', 'base64_data': base64.b64encode(body.getvalue()).decode()}], 'idempotency_key': 'shared'})['materials'][0]
        assert m['asset_id'] == a['id'] and m['variant_id'] is None
        v = ok(client, 'create_variant', {'asset_id': a['id'], 'name': '状态一'})
        w = ok(client, 'create_variant', {'asset_id': a['id'], 'name': '状态二'})
        assert ok(client, 'list_materials', {'asset_id': a['id']})['total'] == 1
        assert ok(client, 'list_materials', {'variant_id': w['id']})['total'] == 1
        v = ok(client, 'set_final_material', {'variant_id': v['id'], 'material_id': m['id'], 'version': v['version']})
        w = ok(client, 'set_final_material', {'variant_id': w['id'], 'material_id': m['id'], 'version': w['version']})
        ok(client, 'delete_variant', {'variant_id': v['id'], 'version': v['version']})
        preview = call(client, 'get_material_preview', {'material_id': m['id']})
        assert preview['content'][0]['type'] == 'image'
        assert ok(client, 'get_variant', {'variant_id': w['id']})['final_material_id'] == m['id']
