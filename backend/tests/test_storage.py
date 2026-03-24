"""Tests for storage.py — image validation, sanitisation, size guard."""
import base64
import io
import os
import pytest
from PIL import Image


def _make_png_b64(w=8, h=8, color=(255, 100, 50)):
    img = Image.new('RGB', (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def test_valid_image_saved(app):
    with app.app_context():
        from storage import save_image
        rel = save_image(_make_png_b64(), project_id=1, run_id=1, step=0, key='pred')
        abs_path = os.path.join(app.config['FILES_DIR'], rel)
        assert os.path.exists(abs_path)


def test_invalid_binary_raises(app):
    with app.app_context():
        from storage import save_image
        bad = base64.b64encode(b'not-an-image-at-all').decode()
        with pytest.raises(ValueError, match='[Ii]nvalid image'):
            save_image(bad, 1, 1, 0, 'x')


def test_oversized_payload_raises(app):
    with app.app_context():
        from storage import save_image
        big = 'A' * 28_000_000
        with pytest.raises(ValueError, match='too large'):
            save_image(big, 1, 1, 0, 'x')


def test_invalid_base64_raises(app):
    with app.app_context():
        from storage import save_image
        with pytest.raises(ValueError, match='[Ii]nvalid base64'):
            save_image('not!!valid@@base64', 1, 1, 0, 'x')


def test_key_sanitisation(app):
    with app.app_context():
        from storage import save_image
        rel = save_image(_make_png_b64(), project_id=1, run_id=1, step=5,
                         key='../../../etc/passwd')
        # Path must not contain traversal sequences — the key is flattened, not used as a path
        assert '..' not in rel
        # The path components are only the integer IDs; the key is only in the filename
        parts = rel.replace('\\', '/').split('/')
        assert parts[0] == '1'    # project_id
        assert parts[1] == '1'    # run_id
        filename = parts[2]
        assert filename.startswith('5_')   # step prefix


def test_path_uses_integer_ids(app):
    with app.app_context():
        from storage import save_image
        rel = save_image(_make_png_b64(), project_id=42, run_id=7, step=3, key='img')
        assert rel.startswith('42/7/')
