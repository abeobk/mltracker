"""Tests for run CRUD + metrics/images query (session auth)."""
import base64
import io
from PIL import Image


def _bearer(key):
    return {'Authorization': f'Bearer {key}'}


def _make_png_b64(w=4, h=4):
    img = Image.new('RGB', (w, h), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def _setup_run(client, api_key, steps=3):
    r = client.post('/api/v1/runs',
                    json={'project': 'test-proj', 'name': 'test-run'},
                    headers=_bearer(api_key))
    ids    = r.get_json()
    run_id = ids['run_id']
    for s in range(steps):
        client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': s, 'loss': 1.0 - s * 0.1, 'acc': s * 0.1,
                          'images': {'pred': _make_png_b64()}},
                    headers=_bearer(api_key))
    return ids


def test_list_runs_own_project(auth_client, client, api_key):
    ids = _setup_run(client, api_key)
    r   = auth_client.get(f"/api/v1/projects/{ids['project_id']}/runs")
    assert r.status_code == 200
    assert len(r.get_json()) == 1


def test_list_runs_other_project_returns_404(auth_client, client, other_api_key):
    ids = _setup_run(client, other_api_key)
    r   = auth_client.get(f"/api/v1/projects/{ids['project_id']}/runs")
    assert r.status_code == 404


def test_get_run_metadata(auth_client, client, api_key):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}')
    assert r.status_code == 200
    d = r.get_json()
    assert d['id'] == run_id
    assert d['status'] == 'running'


def test_metric_keys(auth_client, client, api_key):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/metric-keys')
    assert r.status_code == 200
    keys = r.get_json()
    assert set(keys) == {'loss', 'acc'}


def test_get_metrics_all_keys(auth_client, client, api_key):
    ids    = _setup_run(client, api_key, steps=3)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/metrics')
    assert r.status_code == 200
    data = r.get_json()
    assert 'loss' in data['metrics']
    assert len(data['metrics']['loss']) == 3


def test_get_metrics_key_filter(auth_client, client, api_key):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/metrics?keys=loss')
    data   = r.get_json()
    assert 'loss' in data['metrics']
    assert 'acc' not in data['metrics']


def test_get_metrics_other_user_returns_404(auth_client, client, other_api_key):
    ids    = _setup_run(client, other_api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/metrics')
    assert r.status_code == 404


def test_image_keys(auth_client, client, api_key):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/image-keys')
    assert r.status_code == 200
    assert r.get_json() == ['pred']


def test_get_images(auth_client, client, api_key):
    ids    = _setup_run(client, api_key, steps=3)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/images?key=pred')
    assert r.status_code == 200
    data = r.get_json()
    assert data['total'] == 3
    assert len(data['images']) == 3
    assert data['images'][0]['url'].startswith('/files/')


def test_get_images_missing_key_returns_400(auth_client, client, api_key):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.get(f'/api/v1/runs/{run_id}/images')
    assert r.status_code == 400


def test_delete_run_cascades(auth_client, client, api_key, app):
    ids    = _setup_run(client, api_key)
    run_id = ids['run_id']
    r      = auth_client.delete(f'/api/v1/runs/{run_id}')
    assert r.status_code == 200

    from db import get_db
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone() is None
        assert db.execute("SELECT id FROM metrics WHERE run_id=?", (run_id,)).fetchone() is None
        assert db.execute("SELECT id FROM images WHERE run_id=?", (run_id,)).fetchone() is None


def test_delete_run_other_user_returns_404(auth_client, client, other_api_key):
    ids    = _setup_run(client, other_api_key)
    run_id = ids['run_id']
    r      = auth_client.delete(f'/api/v1/runs/{run_id}')
    assert r.status_code == 404
