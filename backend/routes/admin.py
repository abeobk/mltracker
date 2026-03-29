"""Admin dashboard — session auth, first-user-is-admin rule."""
from flask import Blueprint, jsonify, session
from auth import login_required
from db import get_db

admin_bp = Blueprint('admin', __name__)


def _is_admin(user_id: int) -> bool:
    row = get_db().execute(
        "SELECT MIN(id) AS min_id FROM users WHERE status = 'active'"
    ).fetchone()
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
            u.status,
            CASE WHEN u.google_id IS NOT NULL THEN 'google' ELSE 'password' END AS auth_method,
            u.created_at,
            COUNT(DISTINCT p.id)  AS project_count,
            COUNT(DISTINCT r.id)  AS run_count,
            COALESCE(SUM(
                CASE WHEN r.finished_at IS NOT NULL
                     THEN r.finished_at - r.created_at
                     ELSE 0
                END
            ), 0)                 AS total_run_seconds,
            MAX(COALESCE(r.finished_at, r.created_at)) AS last_active
        FROM users u
        LEFT JOIN projects p ON p.user_id = u.id
        LEFT JOIN runs r     ON r.project_id = p.id
        GROUP BY u.id
        ORDER BY u.id
    """).fetchall()

    return jsonify([dict(r) for r in rows])


@admin_bp.post('/admin/users/<int:user_id>/approve')
@admin_required
def approve_user(user_id):
    my_id = session['user']['id']
    if user_id == my_id:
        return jsonify({'error': 'Cannot modify your own account'}), 400

    db = get_db()
    cur = db.execute(
        "UPDATE users SET status = 'active' WHERE id = ? AND status IN ('pending_approval', 'suspended')",
        (user_id,),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'User not found or already active'}), 404
    return jsonify({'ok': True})


@admin_bp.post('/admin/users/<int:user_id>/suspend')
@admin_required
def suspend_user(user_id):
    my_id = session['user']['id']
    if user_id == my_id:
        return jsonify({'error': 'Cannot suspend your own account'}), 400

    db = get_db()
    cur = db.execute(
        "UPDATE users SET status = 'suspended' WHERE id = ? AND status = 'active'",
        (user_id,),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'User not found or not active'}), 404
    return jsonify({'ok': True})


@admin_bp.delete('/admin/users/<int:user_id>')
@admin_required
def delete_user(user_id):
    my_id = session['user']['id']
    if user_id == my_id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    db = get_db()
    cur = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'ok': True})
