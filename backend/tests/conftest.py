import os
import secrets
import pytest

# Make sure backend/ is on sys.path so imports work
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app
from config import TestConfig
from db import get_db


@pytest.fixture()
def app(tmp_path):
    """App with a temp DB and files dir. Unique per test via tmp_path."""
    class Cfg(TestConfig):
        DB_PATH   = str(tmp_path / 'test.db')
        FILES_DIR = str(tmp_path / 'files')

    application = create_app(Cfg)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def api_key(app):
    """Seed a user and return their API key. Bypasses OAuth."""
    key = secrets.token_hex(32)
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users(google_id, email, name, api_key) VALUES (?,?,?,?)",
            ('test-google-id', 'test@example.com', 'Test User', key),
        )
        db.commit()
    return key


@pytest.fixture()
def auth_client(client, app, api_key):
    """Test client with an authenticated session cookie."""
    with app.app_context():
        db  = get_db()
        row = db.execute("SELECT id FROM users WHERE api_key = ?", (api_key,)).fetchone()
        uid = row['id']

    with client.session_transaction() as sess:
        sess['user'] = {
            'id':      uid,
            'email':   'test@example.com',
            'name':    'Test User',
            'picture': None,
        }
    return client


@pytest.fixture()
def other_api_key(app):
    """Second user for ownership tests."""
    key = secrets.token_hex(32)
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users(google_id, email, name, api_key) VALUES (?,?,?,?)",
            ('other-google-id', 'other@example.com', 'Other User', key),
        )
        db.commit()
    return key
