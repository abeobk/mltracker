"""Run CRUD + data query — session auth."""
import json
from flask import Blueprint, jsonify, session, request

from auth import login_required
from db import get_db
from storage import delete_run_files

runs_bp = Blueprint('runs', __name__)

IMAGE_CAP  = 500
MAX_POINTS_DEFAULT = 1000
MAX_POINTS_LIMIT   = 5000


def _err(msg, code=400):
    return jsonify({'error': msg}), code


def _owned_run(run_id, user_id):
    return get_db().execute(
        """SELECT r.id, r.project_id, r.name, r.status, r.config,
                  r.created_at, r.finished_at
           FROM runs r
           JOIN projects p ON p.id = r.project_id
           WHERE r.id = ? AND p.user_id = ?""",
        (run_id, user_id),
    ).fetchone()


def _owned_project(project_id, user_id):
    return get_db().execute(
        "SELECT id FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<project_id>/runs
# ---------------------------------------------------------------------------
@runs_bp.get('/projects/<int:project_id>/runs')
@login_required
def list_runs(project_id):
    user_id = session['user']['id']
    if not _owned_project(project_id, user_id):
        return _err('Project not found', 404)
    rows = get_db().execute(
        """SELECT id, name, status, created_at, finished_at
           FROM runs WHERE project_id = ? ORDER BY created_at DESC""",
        (project_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>')
@login_required
def get_run(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)
    d = dict(row)
    # Parse config JSON blob gracefully
    if d.get('config'):
        try:
            d['config'] = json.loads(d['config'])
        except Exception:
            d['config'] = None
    return jsonify(d)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/metric-keys
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/metric-keys')
@login_required
def metric_keys(run_id):
    user_id = session['user']['id']
    if not _owned_run(run_id, user_id):
        return _err('Run not found', 404)
    rows = get_db().execute(
        "SELECT DISTINCT key FROM metrics WHERE run_id = ? ORDER BY key",
        (run_id,),
    ).fetchall()
    return jsonify([r['key'] for r in rows])


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/metrics?keys=loss,acc&max_points=1000
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/metrics')
@login_required
def get_metrics(run_id):
    user_id = session['user']['id']
    if not _owned_run(run_id, user_id):
        return _err('Run not found', 404)

    # Parse max_points
    try:
        max_pts = int(request.args.get('max_points', MAX_POINTS_DEFAULT))
        max_pts = max(1, min(max_pts, MAX_POINTS_LIMIT))
    except (ValueError, TypeError):
        max_pts = MAX_POINTS_DEFAULT

    # Parse keys filter
    keys_param = request.args.get('keys', '')
    key_list   = [k.strip() for k in keys_param.split(',') if k.strip()] if keys_param else []

    db = get_db()

    if key_list:
        placeholders = ','.join('?' * len(key_list))
        all_keys = [r['key'] for r in db.execute(
            f"SELECT DISTINCT key FROM metrics WHERE run_id = ? AND key IN ({placeholders})",
            [run_id, *key_list],
        ).fetchall()]
    else:
        all_keys = [r['key'] for r in db.execute(
            "SELECT DISTINCT key FROM metrics WHERE run_id = ? ORDER BY key",
            (run_id,),
        ).fetchall()]

    result     = {}
    downsampled = False

    for key in all_keys:
        count = db.execute(
            "SELECT COUNT(*) FROM metrics WHERE run_id = ? AND key = ?",
            (run_id, key),
        ).fetchone()[0]

        if count > max_pts:
            downsampled = True
            # Even-interval downsampling: select every Nth row
            n = max(1, count // max_pts)
            rows = db.execute(
                """SELECT step, value FROM (
                       SELECT step, value,
                              ROW_NUMBER() OVER (ORDER BY step) - 1 AS rn
                       FROM metrics WHERE run_id = ? AND key = ?
                   ) WHERE rn % ? = 0
                   ORDER BY step""",
                (run_id, key, n),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT step, value FROM metrics WHERE run_id = ? AND key = ? ORDER BY step",
                (run_id, key),
            ).fetchall()

        result[key] = [{'step': r['step'], 'value': r['value']} for r in rows]

    return jsonify({'metrics': result, 'downsampled': downsampled})


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/image-keys
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/image-keys')
@login_required
def image_keys(run_id):
    user_id = session['user']['id']
    if not _owned_run(run_id, user_id):
        return _err('Run not found', 404)
    rows = get_db().execute(
        "SELECT DISTINCT key FROM images WHERE run_id = ? ORDER BY key",
        (run_id,),
    ).fetchall()
    return jsonify([r['key'] for r in rows])


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/images?key=input
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/images')
@login_required
def get_images(run_id):
    user_id = session['user']['id']
    if not _owned_run(run_id, user_id):
        return _err('Run not found', 404)

    key = request.args.get('key', '').strip()
    if not key:
        return _err("'key' query parameter is required")

    db    = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM images WHERE run_id = ? AND key = ?",
        (run_id, key),
    ).fetchone()[0]

    # Return last IMAGE_CAP steps if total exceeds cap
    rows = db.execute(
        """SELECT step, path FROM images WHERE run_id = ? AND key = ?
           ORDER BY step DESC LIMIT ?""",
        (run_id, key, IMAGE_CAP),
    ).fetchall()
    rows = list(reversed(rows))   # back to ascending order

    items = [{'step': r['step'], 'url': f"/files/{r['path']}"} for r in rows]
    return jsonify({'images': items, 'total': total})


# ---------------------------------------------------------------------------
# DELETE /api/v1/runs/<run_id>
# ---------------------------------------------------------------------------
@runs_bp.delete('/runs/<int:run_id>')
@login_required
def delete_run(run_id):
    user_id = session['user']['id']
    # Fetch project_id BEFORE deleting (needed for disk path)
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)
    project_id = row['project_id']

    db  = get_db()
    cur = db.execute(
        """DELETE FROM runs WHERE id = ? AND project_id IN (
               SELECT id FROM projects WHERE user_id = ?
           )""",
        (run_id, user_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return _err('Run not found', 404)

    delete_run_files(project_id, run_id)
    return jsonify({'ok': True})
