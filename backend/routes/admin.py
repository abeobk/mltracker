"""Admin dashboard — session auth, first-user-is-admin rule."""
from flask import Blueprint, jsonify, session
from auth import login_required
from db import get_db

admin_bp = Blueprint('admin', __name__)


def _is_admin(user_id: int) -> bool:
    row = get_db().execute("SELECT MIN(id) AS min_id FROM users").fetchone()
    return row and row['min_id'] == user_id


def admin_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not _is_admin(session['user']['id']):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper


@admin_bp.get('/admin/users')
@admin_required
def list_users():
    rows = get_db().execute("""
        SELECT
            u.id,
            u.email,
            u.name,
            u.picture,
            u.created_at,
            COUNT(DISTINCT p.id)  AS project_count,
            COUNT(DISTINCT r.id)  AS run_count,
            COALESCE(SUM(
                CASE WHEN r.finished_at IS NOT NULL
                     THEN r.finished_at - r.created_at
                     ELSE 0
                END
            ), 0)                 AS total_run_seconds,
            MAX(r.created_at)     AS last_active
        FROM users u
        LEFT JOIN projects p ON p.user_id = u.id
        LEFT JOIN runs r     ON r.project_id = p.id
        GROUP BY u.id
        ORDER BY u.id
    """).fetchall()

    return jsonify([dict(r) for r in rows])
