import base64
import io
import os
import shutil

from PIL import Image
from flask import current_app

MAX_B64_BYTES = 27_000_000   # ~20 MB decoded
MAX_IMAGE_PIXELS = 50_000_000  # ~7 GB uncompressed RGB — decompression bomb cap


def _safe_key(key: str) -> str:
    """Replace every char that is not alphanumeric, '-', or '_' with '_'."""
    return ''.join(c if c.isalnum() or c in '-_' else '_' for c in key)


def save_image(data_b64: str, project_id: int, run_id: int, step: int, key: str) -> str:
    """
    Validate, decode, and save a base64-encoded PNG image.
    Returns the relative path (relative to FILES_DIR) for DB storage.
    Raises ValueError for any validation failure.
    """
    if len(data_b64) > MAX_B64_BYTES:
        raise ValueError("Image payload too large (max ~20 MB)")

    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise ValueError("Invalid base64 encoding")

    # Cap pixel count BEFORE Image.open() — blocks decompression bomb attacks
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()   # force full decode (lazy open won't catch bomb until here)
    except Image.DecompressionBombError:
        raise ValueError("Image dimensions too large")
    except Exception:
        raise ValueError("Invalid image data")

    img = img.convert('RGB')  # normalise format; drop alpha channel

    safe = _safe_key(key)
    rel_path = f"{project_id}/{run_id}/{step}_{safe}.png"
    abs_path = os.path.join(current_app.config['FILES_DIR'], rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    img.save(abs_path, format='PNG')
    return rel_path


def delete_run_files(project_id: int, run_id: int) -> None:
    run_dir = os.path.join(current_app.config['FILES_DIR'],
                           str(project_id), str(run_id))
    shutil.rmtree(run_dir, ignore_errors=True)


def delete_project_files(project_id: int) -> None:
    proj_dir = os.path.join(current_app.config['FILES_DIR'], str(project_id))
    shutil.rmtree(proj_dir, ignore_errors=True)
