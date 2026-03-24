"""Write endpoints — authenticated via API key (Bearer token)."""
import json
import os
from typing import Any
from flask import Blueprint, request, jsonify, g, current_app

from auth import api_key_required
from db import get_db
from limiter import limiter
from storage import save_image, append_metrics

api_bp = Blueprint('api', __name__)

MAX_NAME_LEN    = 200
MAX_KEY_LEN     = 200
MAX_STEP        = 10_000_000
MAX_CONFIG_KB   = 64 * 1024   # 64 KB
MAX_IMAGES_STEP = 20

# ---------------------------------------------------------------------------
# In-process run-info cache — eliminates ownership JOIN on every log() call.
# Key: (run_id, user_id)  →  dict(project_name, run_name, project_id)
# Only 'running' runs are cached.  Evicted by finish_run / resume_run.
# ---------------------------------------------------------------------------
_RUN_CACHE: dict[tuple, dict] = {}


def _err(msg, code=400):
    return jsonify({'error': msg}), code


def _rollback_images(project_name: str, run_name: str, filenames: list) -> None:
    """Delete saved image files on partial failure."""
    from storage import _safe_name
    images_dir = os.path.join(
        current_app.config['FILES_DIR'],
        _safe_name(project_name), _safe_name(run_name), 'images',
    )
    for fname in filenames:
        path = os.path.join(images_dir, fname)
        if os.path.exists(path):
            os.remove(path)


def _get_owned_run(run_id, user_id):
    """Return run info dict if run is owned by user_id, else None.

    For 'running' runs the result is cached in _RUN_CACHE so subsequent
    log() calls make zero SQLite queries (only a dict lookup).
    """
    cache_key = (run_id, user_id)
    cached = _RUN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    row = get_db().execute(
        """SELECT r.id, r.project_id, r.status, r.name AS run_name, p.name AS project_name
           FROM runs r
           JOIN projects p ON p.id = r.project_id
           WHERE r.id = ? AND p.user_id = ?""",
        (run_id, user_id),
    ).fetchone()

    if row and row['status'] == 'running':
        # Store as plain dict — sqlite3.Row becomes invalid after connection closes
        _RUN_CACHE[cache_key] = {
            'id':           row['id'],
            'project_id':   row['project_id'],
            'status':       row['status'],
            'run_name':     row['run_name'],
            'project_name': row['project_name'],
        }

    return row


# ---------------------------------------------------------------------------
# POST /api/v1/runs  — create or return existing run
# ---------------------------------------------------------------------------
@api_bp.post('/runs')
@limiter.limit('60 per minute')
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
#
# Accepts two formats:
#   Single step : {step, ts?, scalar_key: value, ..., images: {key: b64}}
#   Batch       : {steps: [{...}, {...}, ...]}  ← SDK sends this when it
#                 has accumulated multiple steps faster than one POST
# ---------------------------------------------------------------------------
@api_bp.post('/runs/<int:run_id>/log')
@limiter.limit('600 per minute')
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

    if 'steps' in data:
        # ── Batch format ─────────────────────────────────────────────────
        steps_list = data['steps']
        if not isinstance(steps_list, list) or not steps_list:
            return _err('steps must be a non-empty list')
        for step_data in steps_list:
            err = _log_single_step(run, step_data)
            if err is not None:
                return err
    else:
        # ── Single-step format ────────────────────────────────────────────
        err = _log_single_step(run, data)
        if err is not None:
            return err

    return jsonify({'ok': True})


def _log_single_step(run: dict, data: Any):
    """Validate and write one step payload.

    Returns None on success, or a Flask error response on failure.
    """
    if not isinstance(data, dict):
        return _err('each step must be a JSON object')

    step = data.get('step', 0)
    if not isinstance(step, int) or step < 0 or step > MAX_STEP:
        return _err(f'step must be a non-negative integer ≤ {MAX_STEP}')

    # ts comes from the client (time.time() at commit) — store as-is
    ts = data.get('ts')
    if ts is not None and not isinstance(ts, (int, float)):
        ts = None   # ignore malformed ts

    images_raw = data.get('images', {})
    if not isinstance(images_raw, dict):
        return _err('images must be a JSON object')
    if len(images_raw) > MAX_IMAGES_STEP:
        return _err(f'Too many images per step (max {MAX_IMAGES_STEP})')

    for k, v in images_raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            return _err('images must be {string: string} pairs')

    # Collect scalar metrics — skip reserved keys
    scalars = {}
    for k, v in data.items():
        if k in ('step', 'ts', 'images'):
            continue
        if not isinstance(k, str) or not k.strip():
            continue
        if len(k) > MAX_KEY_LEN:
            return _err(f'metric key too long (max {MAX_KEY_LEN} chars): {k!r}')
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scalars[k] = float(v)

    project_name = run['project_name']
    run_name     = run['run_name']

    saved_filenames = []
    image_refs      = {}
    try:
        for key, b64 in images_raw.items():
            if not key.strip():
                return _err('image key must not be empty')
            if len(key) > MAX_KEY_LEN:
                return _err(f'image key too long: {key!r}')
            filename = save_image(b64, project_name, run_name, step, key)
            saved_filenames.append(filename)
            image_refs[key] = filename

        # One JSONL line per step (OS-buffered append — no fsync)
        append_metrics(project_name, run_name, step, scalars,
                       image_refs or None, ts)

    except ValueError as exc:
        _rollback_images(project_name, run_name, saved_filenames)
        return _err(str(exc))
    except Exception:
        _rollback_images(project_name, run_name, saved_filenames)
        raise

    return None   # success


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
    _RUN_CACHE.pop((run_id, g.user_id), None)   # evict so next log() re-checks status
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
    _RUN_CACHE.pop((run_id, g.user_id), None)   # evict so next log() re-fetches fresh status
    return jsonify({'ok': True, 'run_id': run_id})
