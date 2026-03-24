import secrets
from functools import wraps
from flask import (Blueprint, redirect, url_for, session, jsonify,
                   request, g, current_app)
from authlib.integrations.flask_client import OAuth
from db import get_db

auth_bp = Blueprint('auth', __name__)
oauth    = OAuth()


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
        row = get_db().execute(
            "SELECT id FROM users WHERE api_key = ?", (key,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Invalid API key'}), 401
        g.user_id = row['id']
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.get('/login')
def login():
    redirect_uri = url_for('auth.callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.get('/callback')
def callback():
    token    = oauth.google.authorize_access_token()   # validates state automatically
    info     = token.get('userinfo') or oauth.google.userinfo()
    google_id = info['sub']
    email     = info['email']
    name      = info.get('name')
    picture   = info.get('picture')

    db = get_db()
    # TOCTOU-safe upsert
    db.execute(
        "INSERT OR IGNORE INTO users(google_id, email, name, picture, api_key) VALUES (?,?,?,?,?)",
        (google_id, email, name, picture, secrets.token_hex(32)),
    )
    db.commit()
    user_row = db.execute(
        "SELECT id, api_key FROM users WHERE google_id = ?", (google_id,)
    ).fetchone()

    # Update name/picture in case they changed
    db.execute(
        "UPDATE users SET email=?, name=?, picture=? WHERE id=?",
        (email, name, picture, user_row['id']),
    )
    db.commit()

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
    row  = get_db().execute(
        "SELECT api_key FROM users WHERE id = ?", (user['id'],)
    ).fetchone()
    if not row:
        session.clear()
        return jsonify({'logged_in': False})
    return jsonify({**user, 'api_key': row['api_key'], 'logged_in': True})


@auth_bp.post('/regenerate-key')
@login_required
def regenerate_key():
    new_key = secrets.token_hex(32)
    db = get_db()
    db.execute("UPDATE users SET api_key = ? WHERE id = ?",
               (new_key, session['user']['id']))
    db.commit()
    return jsonify({'api_key': new_key})
