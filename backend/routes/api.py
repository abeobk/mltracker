"""Write endpoints — authenticated via API key (Bearer token)."""
import json
import os
from flask import Blueprint, request, jsonify, g, current_app

from auth import api_key_required
from db import get_db
from storage import save_image, delete_run_files

api_bp = Blueprint('api', __name__)

MAX_NAME_LEN   = 200
MAX_KEY_LEN    = 200
MAX_STEP       = 10_000_000
MAX_CONFIG_KB  = 64 * 1024   # 64 KB
MAX_IMAGES_STEP = 20


def _err(msg, code=400):
    return jsonify({'error': msg}), code


def _get_owned_run(run_id, user_id):
    """Return run row if it exists and belongs to user_id, else None."""
    return get_db().execute(
        """SELECT r.id, r.project_id, r.status
           FROM runs r
           JOIN projects p ON p.id = r.project_id
           WHERE r.id = ? AND p.user_id = ?""",
        (run_id, user_id),
    ).fetchone()


# ---------------------------------------------------------------------------
# POST /api/v1/runs  — create or return existing run
# ---------------------------------------------------------------------------
@api_bp.post('/runs')
@api_key_required
def create_run():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return _err('Request body must be a JSON object')

    project_name = data.get('project', '')
    run_name     = data.get('name', '')
    config_val   = data.get('config')

    if not isinstance(project_name, str) or not project_name.strip():
        return _err('project name is required')
    if len(project_name) > MAX_NAME_LEN:
        return _err(f'project name too long (max {MAX_NAME_LEN} chars)')
    if not isinstance(run_name, str) or not run_name.strip():
        return _err('run name is required')
    if len(run_name) > MAX_NAME_LEN:
        return _err(f'run name too long (max {MAX_NAME_LEN} chars)')

    config_json = None
    if config_val is not None:
        config_json = json.dumps(config_val)
        if len(config_json.encode()) > MAX_CONFIG_KB:
            return _err('config too large (max 64 KB)')

    db = get_db()
    # TOCTOU-safe upsert for project
    db.execute(
        "INSERT OR IGNORE INTO projects(user_id, name) VALUES (?, ?)",
        (g.user_id, project_name),
    )
    db.commit()
    project = db.execute(
        "SELECT id FROM projects WHERE user_id = ? AND name = ?",
        (g.user_id, project_name),
    ).fetchone()

    # TOCTOU-safe upsert for run
    db.execute(
        "INSERT OR IGNORE INTO runs(project_id, name, config) VALUES (?, ?, ?)",
        (project['id'], run_name, config_json),
    )
    db.commit()
    run = db.execute(
        "SELECT id FROM runs WHERE project_id = ? AND name = ?",
        (project['id'], run_name),
    ).fetchone()

    return jsonify({'run_id': run['id'], 'project_id': project['id']})


# ---------------------------------------------------------------------------
# POST /api/v1/runs/<run_id>/log
# ---------------------------------------------------------------------------
@api_bp.post('/runs/<int:run_id>/log')
@api_key_required
def log_step(run_id):
    run = _get_owned_run(run_id, g.user_id)
    if not run:
        return _err('Run not found', 404)

    if run['status'] != 'running':
        return _err('Run is terminated. Call /resume to continue logging.', 409)

    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return _err('Request body must be a JSON object')

    step = data.get('step', 0)
    if not isinstance(step, int) or step < 0 or step > MAX_STEP:
        return _err(f'step must be a non-negative integer ≤ {MAX_STEP}')

    images_raw = data.get('images', {})
    if not isinstance(images_raw, dict):
        return _err('images must be a JSON object')
    if len(images_raw) > MAX_IMAGES_STEP:
        return _err(f'Too many images per step (max {MAX_IMAGES_STEP})')

    # Validate image key types
    for k, v in images_raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            return _err('images must be {string: string} pairs')

    # Collect scalar metrics
    scalars = {}
    for k, v in data.items():
        if k in ('step', 'images'):
            continue
        if not isinstance(k, str) or not k.strip():
            continue
        if len(k) > MAX_KEY_LEN:
            return _err(f'metric key too long (max {MAX_KEY_LEN} chars): {k!r}')
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scalars[k] = float(v)

    # Save images — track paths for rollback on failure
    project_id  = run['project_id']
    saved_paths = []
    image_rows  = []
    try:
        for key, b64 in images_raw.items():
            if not key.strip():
                return _err('image key must not be empty')
            if len(key) > MAX_KEY_LEN:
                return _err(f'image key too long: {key!r}')
            rel = save_image(b64, project_id, run_id, step, key)
            saved_paths.append(rel)
            image_rows.append((run_id, step, key, rel))

        db = get_db()
        with db:
            if scalars:
                db.executemany(
                    "INSERT OR REPLACE INTO metrics(run_id, step, key, value) VALUES (?,?,?,?)",
                    [(run_id, step, k, v) for k, v in scalars.items()],
                )
            if image_rows:
                db.executemany(
                    "INSERT OR REPLACE INTO images(run_id, step, key, path) VALUES (?,?,?,?)",
                    image_rows,
                )
    except ValueError as exc:
        # Roll back saved image files
        for rel in saved_paths:
            abs_path = os.path.join(current_app.config['FILES_DIR'], rel)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        return _err(str(exc))
    except Exception:
        for rel in saved_paths:
            abs_path = os.path.join(current_app.config['FILES_DIR'], rel)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        raise

    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# POST /api/v1/runs/<run_id>/finish
# ---------------------------------------------------------------------------
@api_bp.post('/runs/<int:run_id>/finish')
@api_key_required
def finish_run(run_id):
    run = _get_owned_run(run_id, g.user_id)
    if not run:
        return _err('Run not found', 404)

    data = request.get_json(force=True) or {}
    status = data.get('status', 'finished')
    if status not in ('finished', 'crashed'):
        return _err("status must be 'finished' or 'crashed'")

    # Idempotent — already terminated is fine
    if run['status'] in ('finished', 'crashed'):
        return jsonify({'ok': True})

    db = get_db()
    db.execute(
        "UPDATE runs SET status=?, finished_at=unixepoch('now') WHERE id=?",
        (status, run_id),
    )
    db.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# POST /api/v1/runs/<run_id>/resume
# ---------------------------------------------------------------------------
@api_bp.post('/runs/<int:run_id>/resume')
@api_key_required
def resume_run(run_id):
    run = _get_owned_run(run_id, g.user_id)
    if not run:
        return _err('Run not found', 404)

    if run['status'] == 'running':
        return _err('Run is already active', 409)

    db = get_db()
    db.execute(
        "UPDATE runs SET status='running', finished_at=NULL WHERE id=?",
        (run_id,),
    )
    db.commit()
    return jsonify({'ok': True, 'run_id': run_id})
