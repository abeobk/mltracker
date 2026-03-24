"""Shared flask-limiter instance — imported by app.py and route modules.

Key function uses the Bearer token for API-key routes (per-user limiting)
and falls back to remote IP for session-auth routes (e.g. /auth/regenerate-key).

Storage is Redis — configured via RATELIMIT_STORAGE_URI in Flask config
(defaults to redis://localhost:6379).  Do NOT use in-memory storage with
multiple Gunicorn workers: each worker keeps its own counter, making the
effective limit N× the configured value.
"""
from flask import request
from flask_limiter import Limiter


def _rate_limit_key() -> str:
    """Return Bearer token if present, else remote IP."""
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        key = auth[7:].strip()
        if key:
            return key
    return request.remote_addr or '127.0.0.1'


# default_limits=[] — no global limit; each route sets its own via @limiter.limit()
limiter = Limiter(key_func=_rate_limit_key, default_limits=[])
