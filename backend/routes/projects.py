"""Project CRUD — session auth."""
from flask import Blueprint, jsonify, session

from auth import login_required
from db import get_db
from storage import delete_project_files

projects_bp = Blueprint('projects', __name__)


def _err(msg, code=400):
    return jsonify({'error': msg}), code


@projects_bp.get('/projects')
@login_required
def list_projects():
    user_id = session['user']['id']
    rows = get_db().execute(
        "SELECT id, name, created_at FROM projects WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@projects_bp.delete('/projects/<int:project_id>')
@login_required
def delete_project(project_id):
    user_id = session['user']['id']
    db  = get_db()
    # Fetch name BEFORE deleting (needed for disk path)
    proj = db.execute(
        "SELECT name FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    if not proj:
        return _err('Project not found', 404)

    cur = db.execute(
        "DELETE FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return _err('Project not found', 404)

    delete_project_files(proj['name'])
    return jsonify({'ok': True})
