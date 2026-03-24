"""
MLTracker client SDK.

Single-file; only stdlib + requests.

Credentials are read from environment variables — never hardcode them:

    export WANDB_API_KEY=<your-key>
    export WANDB_HOST=http://localhost:5000   # optional, defaults to localhost:5000

Usage:
    # New run — suffix is auto-generated and appended to the name
    run = wandb.init(project="mnist", name="exp-001", config={"lr": 0.001})
    print(run.name)   # e.g. "exp-001_a3f2b1"  ← save this to resume later

    # Simple per-step logging (commit=True by default)
    run.log({"loss": 0.3, "acc": 0.9}, step=0)

    # Accumulate across several calls within one step, then commit
    run.log({"loss": 0.3},    commit=False)   # buffer
    run.log({"acc":  0.9},    commit=False)   # buffer
    run.log({"img": pil_img})                 # commit=True — flushes all three

    # log() returns immediately; posts happen in background.
    # finish() waits for all pending posts before marking the run done.
    run.finish()

    # Resume by passing the full suffixed name to wandb.resume()
    run = wandb.resume(project="mnist", name="exp-001_a3f2b1")
    run.log({"loss": 0.2}, step=100)
    run.finish()
"""
from __future__ import annotations

import atexit
import base64
import io
import os
import queue
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

_DEFAULT_HOST  = 'mltracker.abeobk.com'
_CONFIG_FILE   = os.path.join(os.path.expanduser('~'), '.mltracker')

# ---------------------------------------------------------------------------
# Module-level crash / interrupt handling
# ---------------------------------------------------------------------------
# Tracks all active Run objects so the module-level excepthook can mark them
# as crashed when an unhandled exception (including KeyboardInterrupt) occurs.
_active_runs: set = set()
_original_excepthook = sys.excepthook


def _global_excepthook(exc_type, exc_val, exc_tb):
    """Called for any unhandled exception before the interpreter shuts down.

    Marks every active run as crashed so _auto_finish sends the right status.
    """
    for run in list(_active_runs):
        run._crash_status = 'crashed'
    _original_excepthook(exc_type, exc_val, exc_tb)


sys.excepthook = _global_excepthook


def _load_config() -> dict:
    """Load saved credentials from ~/.mltracker (KEY=VALUE format)."""
    cfg = {}
    if os.path.isfile(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        k, _, v = line.partition('=')
                        cfg[k.strip()] = v.strip()
        except OSError:
            pass
    return cfg


def _save_config(cfg: dict) -> None:
    """Persist credentials to ~/.mltracker (mode 600)."""
    try:
        with open(_CONFIG_FILE, 'w') as f:
            f.write('# MLTracker credentials — do not share this file\n')
            for k, v in cfg.items():
                f.write(f'{k}={v}\n')
        os.chmod(_CONFIG_FILE, 0o600)
    except OSError:
        pass


def _resolve_credentials(api_key: Optional[str], host: Optional[str]):
    """
    Return (api_key, host).

    Priority:
      1. Explicit argument passed by the caller
      2. WANDB_API_KEY / WANDB_HOST environment variables
      3. Saved credentials in ~/.mltracker
      4. Interactive prompt (saved for future use)
    """
    cfg = _load_config()

    resolved_key  = api_key or os.environ.get('WANDB_API_KEY', '') or cfg.get('WANDB_API_KEY', '')
    resolved_host = (host or os.environ.get('WANDB_HOST', '') or cfg.get('WANDB_HOST', _DEFAULT_HOST)).rstrip('/')

    if not resolved_key:
        print("MLTracker: no API key found.")
        print(f"  Open your dashboard and copy the API key from the top bar.")
        try:
            resolved_key = input("  Paste API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            resolved_key = ''
        if not resolved_key:
            raise WandBError("API key is required.")

        if not host and not os.environ.get('WANDB_HOST'):
            entered_host = input(f"  Server URL [{_DEFAULT_HOST}]: ").strip()
            if entered_host:
                resolved_host = entered_host.rstrip('/')

        cfg['WANDB_API_KEY'] = resolved_key
        cfg['WANDB_HOST']    = resolved_host
        _save_config(cfg)
        print(f"  Credentials saved to {_CONFIG_FILE}")

    return resolved_key, resolved_host


class WandBError(RuntimeError):
    pass


class Image:
    """Wrap a numpy array or PIL Image for logging.

    Args:
        data:    numpy array (H×W×3 uint8) or PIL Image.
        bgr:     Set True for OpenCV / cv2 arrays (BGR channel order).
                 The channels are flipped to RGB before encoding.
        caption: Optional string stored alongside the image (unused by
                 the server currently, reserved for future display).

    Example:
        import cv2
        frame = cv2.imread("frame.png")          # BGR uint8
        run.log({"frame": wandb.Image(frame, bgr=True)})

        import numpy as np
        arr = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        run.log({"noise": wandb.Image(arr)})
    """

    def __init__(self, data: Any, bgr: bool = False, caption: str = ''):
        self.data    = data
        self.bgr     = bgr
        self.caption = caption


class Run:
    def __init__(self, host: str, api_key: str, run_id: int, project_id: int, name: str):
        self._host       = host.rstrip('/')
        self._api_key    = api_key
        self._run_id     = run_id
        self._project_id = project_id
        self._name       = name
        self._step       = 0
        self._buffer: Dict[str, Any] = {}   # accumulates data when commit=False

        # Reuse TCP connection — avoids per-request handshake overhead
        self._session = requests.Session()
        self._session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        })

        # Async post queue ─────────────────────────────────────────────────
        # log() puts payloads here and returns immediately.
        # _post_worker drains the queue in a background thread, batching
        # multiple pending steps into a single HTTP POST when possible.
        # finish() sends a None sentinel and waits for the thread to stop.
        self._queue: queue.Queue = queue.Queue()
        self._worker_error: Optional[Exception] = None
        self._worker = threading.Thread(target=self._post_worker, daemon=True)
        self._worker.start()

        # Crash / interrupt recovery
        self._finished     = False
        self._crash_status = None        # set to 'crashed' by _global_excepthook
        _active_runs.add(self)
        atexit.register(self._auto_finish)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> int:
        return self._run_id

    @property
    def project_id(self) -> int:
        return self._project_id

    @property
    def name(self) -> str:
        """Full run name including the unique suffix, e.g. 'exp-001_a3f2b1'."""
        return self._name

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _post(self, path: str, body: Any) -> Dict:
        url = f"{self._host}{path}"
        r   = self._session.post(url, json=body, timeout=30)
        if not r.ok:
            err = r.json().get('error', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
            raise WandBError(f"POST {path} failed ({r.status_code}): {err}")
        return r.json()

    # ------------------------------------------------------------------
    # Image encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(value: Any) -> str:
        """Convert a wandb.Image, PIL Image, or numpy array to a base64-encoded PNG string."""
        # Unwrap wandb.Image wrapper
        if isinstance(value, Image):
            raw = value.data
            try:
                import numpy as np
                if isinstance(raw, np.ndarray):
                    if value.bgr and raw.ndim == 3 and raw.shape[2] == 3:
                        raw = raw[..., ::-1]   # BGR → RGB
                    from PIL import Image as PILImage
                    raw = PILImage.fromarray(raw.astype('uint8'))
            except ImportError:
                pass
            value = raw

        try:
            import numpy as np
            if isinstance(value, np.ndarray):
                from PIL import Image as PILImage
                value = PILImage.fromarray(value.astype('uint8'))
        except ImportError:
            pass

        try:
            from PIL import Image as PILImage
            if isinstance(value, PILImage.Image):
                buf = io.BytesIO()
                value.save(buf, format='PNG')
                return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            pass

        raise ValueError(
            f"Cannot encode image of type {type(value).__name__}. "
            "Pass a wandb.Image, PIL Image, or numpy array."
        )

    # ------------------------------------------------------------------
    # Async post worker
    # ------------------------------------------------------------------

    def _post_worker(self) -> None:
        """Background thread: drain the queue and POST steps in batches.

        Batching strategy: block until the first item arrives, then
        greedily drain whatever else is already queued.  This means that
        when log() is called faster than the server can respond, multiple
        steps are bundled into one POST automatically.
        """
        while True:
            # Block until an item (or shutdown sentinel) is available
            item = self._queue.get()

            if item is None:                 # shutdown sentinel from finish()
                return

            batch: List[Dict] = [item]

            # Greedily drain anything already in the queue
            while True:
                try:
                    extra = self._queue.get_nowait()
                    if extra is None:        # sentinel found while draining
                        self._flush_batch(batch)
                        return
                    batch.append(extra)
                except queue.Empty:
                    break

            self._flush_batch(batch)

    def _flush_batch(self, batch: List[Dict]) -> None:
        """POST one or many steps to the server in a single HTTP request."""
        try:
            if len(batch) == 1:
                body = batch[0]              # single step — original format
            else:
                body = {'steps': batch}      # batch format — server processes all

            result = self._post(f'/api/v1/runs/{self._run_id}/log', body)
            if not result.get('ok'):
                self._worker_error = WandBError(f"log() failed: {result}")
        except Exception as exc:
            self._worker_error = exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, data: Dict[str, Any], step: Optional[int] = None,
            commit: bool = True) -> None:
        """Log scalars and/or images for one step.

        commit=False — accumulate data into buffer without sending.
                       Does NOT advance the step counter.
        commit=True  — timestamp + flush buffer + data as one payload,
                       enqueue for async POST, advance step counter.

        Returns immediately regardless of commit value — the actual HTTP
        POST happens in the background thread.

        Example (accumulate pattern):
            run.log({"loss": 0.3}, commit=False)
            run.log({"acc":  0.9}, commit=False)
            run.log({"img":  img})               # flushes all three at step N
        """
        # Accumulate into buffer (later values overwrite earlier for same key)
        self._buffer.update(data)

        if not commit:
            return

        # Capture timestamp at the moment the step is committed
        ts = time.time()

        # Determine step number
        if step is None:
            step = self._step
            self._step += 1
        else:
            self._step = step + 1

        # Build payload from buffer
        payload: Dict[str, Any] = {'step': step, 'ts': ts}
        images: Dict[str, str] = {}

        for key, value in self._buffer.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                payload[key] = value
            else:
                images[key] = self._encode_image(value)

        self._buffer.clear()

        if images:
            payload['images'] = images

        # Enqueue — returns immediately, worker handles the actual POST
        self._queue.put(payload)

    def finish(self, status: str = 'finished') -> None:
        """Flush all pending log data, wait for the background thread to finish,
        then mark the run as finished or crashed on the server.

        Called explicitly at the end of a training script, OR automatically
        by the atexit handler on Ctrl+C / crash (with status='crashed').
        Always call finish() explicitly to get status='finished'.
        """
        if self._finished:
            return                      # guard against double-calls from atexit
        self._finished = True
        _active_runs.discard(self)

        # If there is uncommitted data in the buffer, force a final commit
        if self._buffer:
            self.log({})

        # Send shutdown sentinel; worker stops after processing everything ahead of it
        self._queue.put(None)
        self._worker.join()

        # Re-raise any error that occurred in the background
        if self._worker_error:
            raise self._worker_error

        self._post(f'/api/v1/runs/{self._run_id}/finish', {'status': status})

    def _auto_finish(self) -> None:
        """atexit handler: flush queue and mark run crashed if finish() was never called.

        Runs automatically on Ctrl+C, unhandled exceptions, or any other exit
        that bypasses an explicit run.finish() call.
        """
        if self._finished:
            return                      # finish() was already called — nothing to do
        status = self._crash_status or 'crashed'
        try:
            self.finish(status=status)
        except Exception:
            pass                        # best-effort — server may be unreachable


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_session(api_key: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    })
    return s


def _create_or_get_run(session: requests.Session, host: str, api_key: str,
                       project: str, full_name: str,
                       config: Optional[Dict]) -> Run:
    url  = f"{host}/api/v1/runs"
    body: Dict[str, Any] = {'project': project, 'name': full_name}
    if config is not None:
        body['config'] = config

    r = session.post(url, json=body, timeout=30)
    if not r.ok:
        err = r.json().get('error', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
        raise WandBError(f"Failed to create run ({r.status_code}): {err}")

    data = r.json()
    run  = Run(host=host, api_key=api_key,
               run_id=data['run_id'], project_id=data['project_id'],
               name=full_name)
    run._session = session
    return run


def init(
    project: str,
    name: str,
    config: Optional[Dict] = None,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> Run:
    """
    Create a new run with a unique suffix appended to `name`.

    The full name (e.g. 'exp-001_a3f2b1') is accessible as run.name.
    Save it — you'll need it to resume this run later.

    Credentials are read from environment variables if not passed explicitly:
        WANDB_API_KEY — required
        WANDB_HOST    — optional, defaults to http://localhost:5000
    """
    api_key, host = _resolve_credentials(api_key, host)
    suffix        = secrets.token_hex(3)          # 6-char hex, e.g. "a3f2b1"
    full_name     = f"{name}_{suffix}"
    session       = _make_session(api_key)
    return _create_or_get_run(session, host, api_key, project, full_name, config)


def resume(
    project: str,
    name: str,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
) -> Run:
    """
    Resume a terminated run identified by its full suffixed name
    (e.g. 'exp-001_a3f2b1').

    Retrieves the run from the server, calls /resume to reopen it,
    and returns a Run object ready for logging.

    Credentials are read from environment variables if not passed explicitly:
        WANDB_API_KEY — required
        WANDB_HOST    — optional, defaults to http://localhost:5000
    """
    api_key, host = _resolve_credentials(api_key, host)
    session       = _make_session(api_key)

    # Get or create the run record (idempotent by name)
    run = _create_or_get_run(session, host, api_key, project, name, config=None)

    # Reopen it for logging
    url = f"{host}/api/v1/runs/{run.run_id}/resume"
    r   = session.post(url, json={}, timeout=30)
    if not r.ok:
        err = r.json().get('error', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
        raise WandBError(
            f"resume() failed ({r.status_code}): {err}\n"
            f"Hint: pass the exact suffixed name, e.g. '{name}' — check run.name from init()."
        )

    return run
