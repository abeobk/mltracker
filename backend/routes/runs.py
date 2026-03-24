"""Run CRUD + data query — session auth."""
import json
from flask import Blueprint, jsonify, session, request

from auth import login_required
from db import get_db
from storage import delete_run_files, read_metrics

runs_bp = Blueprint('runs', __name__)

IMAGE_CAP          = 500
MAX_POINTS_DEFAULT = 1000
MAX_POINTS_LIMIT   = 5000


def _err(msg, code=400):
    return jsonify({'error': msg}), code


def _owned_run(run_id, user_id):
    return get_db().execute(
        """SELECT r.id, r.project_id, r.name AS run_name, r.status, r.config,
                  r.created_at, r.finished_at, p.name AS project_name
           FROM runs r
           JOIN projects p ON p.id = r.project_id
           WHERE r.id = ? AND p.user_id = ?""",
        (run_id, user_id),
    ).fetchone()


def _owned_project(project_id, user_id):
    return get_db().execute(
        "SELECT id, name FROM projects WHERE id = ? AND user_id = ?",
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
    if d.get('config'):
        try:
            d['config'] = json.loads(d['config'])
        except Exception:
            d['config'] = None
    return jsonify(d)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/metric-keys  — scalar keys only
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/metric-keys')
@login_required
def metric_keys(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)
    all_rows = read_metrics(row['project_name'], row['run_name'])
    keys = sorted({
        k for r in all_rows for k, v in r.items()
        if k not in ('step', 'ts') and isinstance(v, (int, float)) and not isinstance(v, bool)
    })
    return jsonify(keys)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/metrics?keys=loss,acc&max_points=1000
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/metrics')
@login_required
def get_metrics(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)

    try:
        max_pts = int(request.args.get('max_points', MAX_POINTS_DEFAULT))
        max_pts = max(1, min(max_pts, MAX_POINTS_LIMIT))
    except (ValueError, TypeError):
        max_pts = MAX_POINTS_DEFAULT

    keys_param = request.args.get('keys', '')
    key_set    = {k.strip() for k in keys_param.split(',') if k.strip()} if keys_param else set()

    all_rows = read_metrics(row['project_name'], row['run_name'])
    series: dict = {}
    for r in all_rows:
        step = r.get('step', 0)
        for k, v in r.items():
            if k in ('step', 'ts'):
                continue   # ts is metadata, not a chart metric
            if key_set and k not in key_set:
                continue
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue   # skip image refs and other non-numeric values
            if k not in series:
                series[k] = []
            series[k].append({'step': step, 'value': v})

    result      = {}
    downsampled = False
    for key, points in series.items():
        if len(points) > max_pts:
            downsampled = True
            n = max(1, len(points) // max_pts)
            result[key] = points[::n]
        else:
            result[key] = points

    return jsonify({'metrics': result, 'downsampled': downsampled})


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/image-keys
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/image-keys')
@login_required
def image_keys(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)
    all_rows = read_metrics(row['project_name'], row['run_name'])
    keys = sorted({
        k for r in all_rows for k, v in r.items()
        if k != 'step' and isinstance(v, dict) and v.get('type') == 'image'
    })
    return jsonify(keys)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/<run_id>/images?key=input
# ---------------------------------------------------------------------------
@runs_bp.get('/runs/<int:run_id>/images')
@login_required
def get_images(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)

    key = request.args.get('key', '').strip()
    if not key:
        return _err("'key' query parameter is required")

    project_name = row['project_name']
    run_name     = row['run_name']

    all_rows = read_metrics(project_name, run_name)
    # Collect all steps where this key has an image ref
    entries = []
    for r in all_rows:
        v = r.get(key)
        if isinstance(v, dict) and v.get('type') == 'image':
            from storage import _safe_name
            filename = v['name']
            url_path = f"{_safe_name(project_name)}/{_safe_name(run_name)}/images/{filename}"
            entries.append({'step': r.get('step', 0), 'url': f"/files/{url_path}"})

    total = len(entries)
    # Cap at IMAGE_CAP most recent steps
    if total > IMAGE_CAP:
        entries = entries[-IMAGE_CAP:]

    return jsonify({'images': entries, 'total': total})


# ---------------------------------------------------------------------------
# DELETE /api/v1/runs/<run_id>
# ---------------------------------------------------------------------------
@runs_bp.delete('/runs/<int:run_id>')
@login_required
def delete_run(run_id):
    user_id = session['user']['id']
    row = _owned_run(run_id, user_id)
    if not row:
        return _err('Run not found', 404)
    project_name = row['project_name']
    run_name     = row['run_name']

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

    delete_run_files(project_name, run_name)
    return jsonify({'ok': True})
