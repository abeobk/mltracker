"""Tests for write API endpoints (api_key auth)."""
import base64
import io
import pytest
from PIL import Image


def _bearer(key):
    return {'Authorization': f'Bearer {key}'}


def _make_png_b64(w=10, h=10):
    img = Image.new('RGB', (w, h), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def create_run(client, key, project='proj', name='run-1', config=None):
    body = {'project': project, 'name': name}
    if config:
        body['config'] = config
    return client.post('/api/v1/runs',
                       json=body, headers=_bearer(key))


# ---------------------------------------------------------------------------
# Create run
# ---------------------------------------------------------------------------

def test_create_run_returns_ids(client, api_key):
    r = create_run(client, api_key)
    assert r.status_code == 200
    data = r.get_json()
    assert 'run_id' in data and 'project_id' in data


def test_create_run_idempotent(client, api_key):
    r1 = create_run(client, api_key)
    r2 = create_run(client, api_key)
    assert r1.get_json()['run_id'] == r2.get_json()['run_id']


def test_create_run_missing_name(client, api_key):
    r = client.post('/api/v1/runs',
                    json={'project': 'p'},
                    headers=_bearer(api_key))
    assert r.status_code == 400


def test_create_run_config_too_large(client, api_key):
    big = 'x' * (65 * 1024)
    r = client.post('/api/v1/runs',
                    json={'project': 'p', 'name': 'r', 'config': {'k': big}},
                    headers=_bearer(api_key))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_missing_api_key_returns_401(client):
    r = client.post('/api/v1/runs', json={'project': 'p', 'name': 'r'})
    assert r.status_code == 401


def test_invalid_api_key_returns_401(client):
    r = client.post('/api/v1/runs',
                    json={'project': 'p', 'name': 'r'},
                    headers=_bearer('bad-key'))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

def test_log_scalars(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 0, 'loss': 0.5, 'acc': 0.9},
                    headers=_bearer(api_key))
    assert r.status_code == 200


def test_log_to_other_users_run_returns_404(client, api_key, other_api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 0, 'loss': 0.1},
                    headers=_bearer(other_api_key))
    assert r.status_code == 404


def test_log_to_finished_run_returns_409(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    client.post(f'/api/v1/runs/{run_id}/finish',
                json={'status': 'finished'},
                headers=_bearer(api_key))
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 1, 'loss': 0.2},
                    headers=_bearer(api_key))
    assert r.status_code == 409
    assert 'resume' in r.get_json()['error'].lower()


def test_log_after_resume_succeeds(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    client.post(f'/api/v1/runs/{run_id}/finish',
                json={'status': 'finished'}, headers=_bearer(api_key))
    client.post(f'/api/v1/runs/{run_id}/resume', headers=_bearer(api_key))
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 100, 'loss': 0.1},
                    headers=_bearer(api_key))
    assert r.status_code == 200


def test_log_image(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 0, 'images': {'pred': _make_png_b64()}},
                    headers=_bearer(api_key))
    assert r.status_code == 200


def test_log_image_too_large_rejected(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    big = 'A' * 28_000_000
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': 0, 'images': {'x': big}},
                    headers=_bearer(api_key))
    assert r.status_code == 400


def test_log_invalid_step_rejected(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/log',
                    json={'step': -1, 'loss': 0.1},
                    headers=_bearer(api_key))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Finish
# ---------------------------------------------------------------------------

def test_finish_run(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/finish',
                    json={'status': 'finished'},
                    headers=_bearer(api_key))
    assert r.status_code == 200


def test_finish_invalid_status(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/finish',
                    json={'status': 'done'},
                    headers=_bearer(api_key))
    assert r.status_code == 400


def test_finish_other_users_run_returns_404(client, api_key, other_api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/finish',
                    json={'status': 'finished'},
                    headers=_bearer(other_api_key))
    assert r.status_code == 404


def test_finish_idempotent(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    client.post(f'/api/v1/runs/{run_id}/finish',
                json={'status': 'finished'}, headers=_bearer(api_key))
    r = client.post(f'/api/v1/runs/{run_id}/finish',
                    json={'status': 'finished'}, headers=_bearer(api_key))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def test_resume_already_running_returns_409(client, api_key):
    run_id = create_run(client, api_key).get_json()['run_id']
    r = client.post(f'/api/v1/runs/{run_id}/resume', headers=_bearer(api_key))
    assert r.status_code == 409
