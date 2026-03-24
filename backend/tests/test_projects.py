"""Tests for project CRUD (session auth)."""


def _create_run(client, api_key, project='proj', name='run-1'):
    r = client.post('/api/v1/runs',
                    json={'project': project, 'name': name},
                    headers={'Authorization': f'Bearer {api_key}'})
    return r.get_json()


def test_list_projects_empty(auth_client):
    r = auth_client.get('/api/v1/projects')
    assert r.status_code == 200
    assert r.get_json() == []


def test_list_projects_returns_own_only(auth_client, api_key, other_api_key, client, app):
    # Create a project for our user via API key, another user's project
    _create_run(client, api_key, project='my-proj')
    _create_run(client, other_api_key, project='their-proj')

    r = auth_client.get('/api/v1/projects')
    names = [p['name'] for p in r.get_json()]
    assert 'my-proj' in names
    assert 'their-proj' not in names


def test_delete_project_cascades(auth_client, client, api_key, app):
    ids = _create_run(client, api_key)
    run_id     = ids['run_id']
    project_id = ids['project_id']

    # Log a metric so the run has data
    client.post(f'/api/v1/runs/{run_id}/log',
                json={'step': 0, 'loss': 0.5},
                headers={'Authorization': f'Bearer {api_key}'})

    r = auth_client.delete(f'/api/v1/projects/{project_id}')
    assert r.status_code == 200

    from db import get_db
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone() is None
        assert db.execute("SELECT id FROM metrics WHERE run_id=?", (run_id,)).fetchone() is None


def test_delete_project_not_found(auth_client):
    r = auth_client.delete('/api/v1/projects/9999')
    assert r.status_code == 404


def test_delete_other_users_project_returns_404(auth_client, client, other_api_key):
    ids = _create_run(client, other_api_key, project='other-proj')
    r = auth_client.delete(f"/api/v1/projects/{ids['project_id']}")
    assert r.status_code == 404


def test_unauthenticated_list_redirects(client):
    r = client.get('/api/v1/projects')
    assert r.status_code in (302, 401)
