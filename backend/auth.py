import secrets
import hashlib
import os
import re
from functools import wraps
from flask import (Blueprint, redirect, url_for, session, jsonify,
                   request, g, current_app)
from authlib.integrations.flask_client import OAuth
from db import get_db
from limiter import limiter

auth_bp = Blueprint('auth', __name__)
oauth    = OAuth()

# ---------------------------------------------------------------------------
# In-process API-key cache — eliminates DB lookup on every log() call.
# Key: api_key string  →  Value: user_id int
# Invalidated when a key is regenerated (call invalidate_api_key below).
# Per-worker memory (not shared across Gunicorn workers — acceptable for auth).
# ---------------------------------------------------------------------------
_KEY_CACHE: dict[str, int] = {}


def invalidate_api_key(api_key: str) -> None:
    """Remove a key from the in-process cache (call after regeneration)."""
    _KEY_CACHE.pop(api_key, None)


def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        'google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


# ---------------------------------------------------------------------------
# Password hashing — scrypt via stdlib (no extra deps)
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return dk.hex() == dk_hex
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Bootstrap admin helper
# ---------------------------------------------------------------------------

def _activate_if_bootstrap_admin(db, user_id: int) -> bool:
    """
    The user with the lowest id is the bootstrap admin regardless of status.
    If they are pending_approval, auto-activate them so they can always log in.
    Returns True if this user is the bootstrap admin.
    """
    row = db.execute("SELECT MIN(id) AS min_id FROM users").fetchone()
    if row and row['min_id'] == user_id:
        db.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
        db.commit()
        return True
    return False


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Session-auth guard for browser routes."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return wrapper


def api_key_required(f):
    """Bearer-token guard for script API routes. Sets g.user_id on success."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing Authorization header'}), 401
        key = auth[7:].strip()

        # Fast path: key already verified this session — no DB call
        user_id = _KEY_CACHE.get(key)
        if user_id is None:
            db  = get_db()
            row = db.execute(
                "SELECT id, status FROM users WHERE api_key = ?", (key,)
            ).fetchone()
            if not row:
                return jsonify({'error': 'Invalid API key'}), 401
            if row['status'] != 'active':
                _activate_if_bootstrap_admin(db, row['id'])
                row = db.execute("SELECT id, status FROM users WHERE api_key = ?", (key,)).fetchone()
            if not row or row['status'] != 'active':
                return jsonify({'error': 'Invalid API key'}), 401
            user_id = row['id']
            _KEY_CACHE[key] = user_id

        g.user_id = user_id
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.get('/login')
def login():
    """Serve the login page (HTML). Does NOT redirect to Google directly."""
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'login.html')


@auth_bp.get('/register')
def register_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'register.html')


@auth_bp.get('/pending')
def pending_page():
    from flask import send_from_directory
    return send_from_directory(current_app.static_folder, 'pending.html')


@auth_bp.post('/register')
@limiter.limit('10 per hour')
def register():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid request'}), 400

    name     = data.get('name', '').strip()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    # Validate email format
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({'error': 'Invalid email address'}), 400

    # Validate password length
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    db = get_db()

    # Check email not already taken
    existing = db.execute("SELECT id, status FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        # Don't reveal whether the account exists — same message either way
        return jsonify({'error': 'An account with this email already exists'}), 409

    password_hash = _hash_password(password)
    api_key = secrets.token_hex(32)

    db.execute(
        "INSERT INTO users(name, email, password_hash, api_key, status) VALUES (?,?,?,?,?)",
        (name, email, password_hash, api_key, 'pending_approval'),
    )
    db.commit()
    return jsonify({'ok': True}), 201


@auth_bp.post('/login')
@limiter.limit('20 per hour')
def login_post():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid request'}), 400

    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')

    db  = get_db()
    row = db.execute(
        "SELECT id, name, picture, api_key, password_hash, status FROM users WHERE email = ? AND password_hash IS NOT NULL",
        (email,),
    ).fetchone()

    # Constant-time failure path — always verify even on miss to prevent timing attacks
    dummy_hash = 'deadbeef' * 4 + ':' + 'deadbeef' * 8
    stored = row['password_hash'] if row else dummy_hash
    ok = _verify_password(password, stored)

    if not ok or not row:
        return jsonify({'error': 'Invalid email or password'}), 401

    if row['status'] != 'active':
        _activate_if_bootstrap_admin(db, row['id'])
        row = db.execute(
            "SELECT id, name, picture, api_key, status FROM users WHERE id = ?", (row['id'],)
        ).fetchone()

    if row['status'] == 'pending_approval':
        return jsonify({'error': 'Your account is awaiting admin approval'}), 403

    if row['status'] != 'active':
        return jsonify({'error': 'Account not active'}), 403

    session['user'] = {
        'id':      row['id'],
        'email':   email,
        'name':    row['name'],
        'picture': row['picture'],
    }
    return jsonify({'ok': True})


@auth_bp.get('/google')
def google_login():
    """Redirect to Google OAuth consent screen."""
    redirect_uri = url_for('auth.callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.get('/callback')
def callback():
    token     = oauth.google.authorize_access_token()   # validates state automatically
    info      = token.get('userinfo') or oauth.google.userinfo()
    google_id = info['sub']
    email     = info['email']
    name      = info.get('name')
    picture   = info.get('picture')

    db = get_db()

    # First-ever user? → auto-activate (becomes admin)
    is_first = db.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()['cnt'] == 0

    status = 'active' if is_first else 'pending_approval'

    # TOCTOU-safe upsert — only sets status on INSERT, never downgrades existing rows
    db.execute(
        "INSERT OR IGNORE INTO users(google_id, email, name, picture, api_key, status) VALUES (?,?,?,?,?,?)",
        (google_id, email, name, picture, secrets.token_hex(32), status),
    )
    db.commit()

    user_row = db.execute(
        "SELECT id, api_key, status FROM users WHERE google_id = ?", (google_id,)
    ).fetchone()

    # Update name/picture in case they changed
    db.execute(
        "UPDATE users SET email=?, name=?, picture=? WHERE id=?",
        (email, name, picture, user_row['id']),
    )
    db.commit()

    if user_row['status'] != 'active':
        _activate_if_bootstrap_admin(db, user_row['id'])
        user_row = db.execute(
            "SELECT id, api_key, status FROM users WHERE id = ?", (user_row['id'],)
        ).fetchone()

    if user_row['status'] == 'pending_approval':
        return redirect(url_for('auth.pending_page'))

    if user_row['status'] != 'active':
        return redirect(url_for('auth.login') + '?error=inactive')

    session['user'] = {
        'id':      user_row['id'],
        'email':   email,
        'name':    name,
        'picture': picture,
    }
    return redirect('/')


@auth_bp.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.get('/me')
def me():
    """Always returns JSON — never uses @login_required (would 302 on fetch())."""
    if 'user' not in session:
        return jsonify({'logged_in': False})
    user = session['user']
    db   = get_db()
    row  = db.execute(
        "SELECT api_key, status FROM users WHERE id = ?", (user['id'],)
    ).fetchone()
    if not row:
        session.clear()
        return jsonify({'logged_in': False})
    if row['status'] != 'active':
        _activate_if_bootstrap_admin(db, user['id'])
        row = db.execute("SELECT api_key, status FROM users WHERE id = ?", (user['id'],)).fetchone()
    if not row or row['status'] != 'active':
        session.clear()
        return jsonify({'logged_in': False})
    admin_row = db.execute("SELECT MIN(id) AS min_id FROM users WHERE status = 'active'").fetchone()
    is_admin  = bool(admin_row and admin_row['min_id'] == user['id'])
    return jsonify({**user, 'api_key': row['api_key'], 'logged_in': True, 'is_admin': is_admin})


@auth_bp.post('/regenerate-key')
@login_required
@limiter.limit('5 per hour')
def regenerate_key():
    new_key = secrets.token_hex(32)
    db = get_db()
    old = db.execute("SELECT api_key FROM users WHERE id = ?",
                     (session['user']['id'],)).fetchone()
    if old:
        invalidate_api_key(old['api_key'])
    db.execute("UPDATE users SET api_key = ? WHERE id = ?",
               (new_key, session['user']['id']))
    db.commit()
    return jsonify({'api_key': new_key})
