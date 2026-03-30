from __future__ import annotations
import base64
import io
import json
import os
import shutil

from PIL import Image
from flask import current_app

MAX_B64_BYTES    = 27_000_000   # ~20 MB decoded
MAX_IMAGE_PIXELS = 50_000_000   # ~7 GB uncompressed RGB — decompression bomb cap


def _safe_name(name: str) -> str:
    """Sanitise a project or run name for use as a directory component.

    Keeps alphanumeric, '-', '_', and '.' — replaces everything else with '_'.
    Prevents path traversal via names like '../../etc'.
    """
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)


def _run_dir(project_name: str, run_name: str) -> str:
    """Return the absolute path to a run's data directory.

    Layout: <FILES_DIR>/<project_name>/<run_name>/
    """
    return os.path.join(
        current_app.config['FILES_DIR'],
        _safe_name(project_name),
        _safe_name(run_name),
    )


# ---------------------------------------------------------------------------
# Image storage
# ---------------------------------------------------------------------------

def save_image(data_b64: str, project_name: str, run_name: str,
               step: int, key: str) -> str:
    """Validate, decode, and save a base64-encoded image.

    Saves to: <FILES_DIR>/<project_name>/<run_name>/images/<step>_<key>.png
    Returns the filename only (e.g. '5_pred.png') for inline JSONL reference.
    Raises ValueError for any validation failure.
    """
    if len(data_b64) > MAX_B64_BYTES:
        raise ValueError("Image payload too large (max ~20 MB)")

    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise ValueError("Invalid base64 encoding")

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Image.DecompressionBombError:
        raise ValueError("Image dimensions too large")
    except Exception:
        raise ValueError("Invalid image data")

    img = img.convert('RGB')

    safe_key  = _safe_name(key)
    filename  = f"{step}_{safe_key}.png"
    images_dir = os.path.join(_run_dir(project_name, run_name), 'images')
    os.makedirs(images_dir, exist_ok=True)
    img.save(os.path.join(images_dir, filename), format='PNG')
    return filename


# ---------------------------------------------------------------------------
# Metrics JSONL
# ---------------------------------------------------------------------------

def metrics_path(project_name: str, run_name: str) -> str:
    """Absolute path to the run's metrics.jsonl file."""
    return os.path.join(_run_dir(project_name, run_name), 'metrics.jsonl')


def append_metrics(project_name: str, run_name: str, step: int,
                   scalars: dict, image_refs: dict | None = None,
                   ts: float | None = None) -> None:
    """Append one log line to the run's metrics.jsonl.

    Scalars stored as plain numbers.
    ts (Unix timestamp) stored under 'ts' — excluded from chart queries.
    Image refs stored as {"type": "image", "name": "<filename>"}.

    Example line:
        {"step": 5, "ts": 1718000000.123, "loss": 0.3,
         "pred": {"type": "image", "name": "5_pred.png"}}
    """
    row: dict = {'step': step}
    if ts is not None:
        row['ts'] = ts
    row.update(scalars)
    if image_refs:
        for key, filename in image_refs.items():
            row[key] = {'type': 'image', 'name': filename}

    path = metrics_path(project_name, run_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def read_metrics(project_name: str, run_name: str) -> list:
    """Read all rows from metrics.jsonl.

    Returns a list of dicts. Each dict has 'step', scalar keys (float values),
    and optionally image keys ({"type": "image", "name": "..."} values).
    Silently skips malformed lines.
    """
    path = metrics_path(project_name, run_name)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def delete_run_files(project_name: str, run_name: str) -> None:
    shutil.rmtree(_run_dir(project_name, run_name), ignore_errors=True)


def delete_project_files(project_name: str) -> None:
    proj_dir = os.path.join(current_app.config['FILES_DIR'], _safe_name(project_name))
    shutil.rmtree(proj_dir, ignore_errors=True)
