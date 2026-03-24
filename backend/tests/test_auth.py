"""Tests for auth endpoints."""


def test_health_ok(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'ok'


def test_me_unauthenticated(client):
    r = client.get('/auth/me')
    assert r.status_code == 200
    data = r.get_json()
    assert data['logged_in'] is False


def test_me_authenticated(auth_client, api_key):
    r = auth_client.get('/auth/me')
    assert r.status_code == 200
    data = r.get_json()
    assert data['logged_in'] is True
    assert data['api_key'] == api_key


def test_regenerate_key(auth_client, api_key):
    r = auth_client.post('/auth/regenerate-key')
    assert r.status_code == 200
    new_key = r.get_json()['api_key']
    assert new_key != api_key
    assert len(new_key) == 64  # 32 bytes hex = 64 chars


def test_api_key_in_header_not_query(client, api_key):
    """API key must be rejected if sent as query param (not in header)."""
    r = client.post(f'/api/v1/runs?api_key={api_key}',
                    json={'project': 'p', 'name': 'r'})
    assert r.status_code == 401


def test_logout_clears_session(auth_client):
    r = auth_client.get('/auth/logout', follow_redirects=False)
    assert r.status_code in (302, 303)
    r2 = auth_client.get('/auth/me')
    assert r2.get_json()['logged_in'] is False
