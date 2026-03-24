"""
WandB Clone client SDK.

Single-file; only stdlib + requests.

Usage:
    run = wandb.init(project="mnist", name="exp-001",
                     api_key="<key>", host="http://localhost:5000")
    run.log({"loss": 0.3, "acc": 0.9}, step=0)
    run.finish()

    # Resume a finished/crashed run:
    run.resume()
    run.log({"loss": 0.2}, step=100)
    run.finish()
"""
from __future__ import annotations

import base64
import io
from typing import Any, Dict, Optional

import requests


class WandBError(RuntimeError):
    pass


class Run:
    def __init__(self, host: str, api_key: str, run_id: int, project_id: int):
        self._host       = host.rstrip('/')
        self._api_key    = api_key
        self._run_id     = run_id
        self._project_id = project_id
        self._step       = 0

    @property
    def run_id(self) -> int:
        return self._run_id

    @property
    def project_id(self) -> int:
        return self._project_id

    def _headers(self) -> Dict[str, str]:
        return {'Authorization': f'Bearer {self._api_key}',
                'Content-Type': 'application/json'}

    def _post(self, path: str, body: Any) -> Dict:
        url = f"{self._host}{path}"
        r   = requests.post(url, json=body, headers=self._headers(), timeout=30)
        if not r.ok:
            err = r.json().get('error', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
            raise WandBError(f"POST {path} failed ({r.status_code}): {err}")
        return r.json()

    @staticmethod
    def _encode_image(value: Any) -> str:
        """Convert a PIL Image or numpy array to a base64-encoded PNG string."""
        # Try numpy → PIL first
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
            "Pass a PIL Image or numpy array."
        )

    def log(self, data: Dict[str, Any], step: Optional[int] = None) -> None:
        """Log scalars and/or images for one step."""
        if step is None:
            step = self._step
            self._step += 1
        else:
            self._step = step + 1

        payload: Dict[str, Any] = {'step': step}
        images: Dict[str, str] = {}

        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                payload[key] = value
            else:
                # Attempt image encoding
                images[key] = self._encode_image(value)

        if images:
            payload['images'] = images

        result = self._post(f'/api/v1/runs/{self._run_id}/log', payload)
        if not result.get('ok'):
            raise WandBError(f"log() failed: {result}")

    def finish(self, status: str = 'finished') -> None:
        """Mark the run as finished or crashed."""
        self._post(f'/api/v1/runs/{self._run_id}/finish', {'status': status})

    def resume(self) -> None:
        """Re-open a terminated run so log() is accepted again."""
        try:
            self._post(f'/api/v1/runs/{self._run_id}/resume', {})
        except WandBError as e:
            raise WandBError(
                f"resume() failed: {e}\n"
                "Hint: the run may already be in 'running' state."
            ) from e


def init(
    project: str,
    name: str,
    api_key: str,
    host: str = 'http://localhost:5000',
    config: Optional[Dict] = None,
) -> Run:
    """
    Create (or retrieve) a run on the server and return a Run object.

    Parameters
    ----------
    project : project name (created automatically if it doesn't exist)
    name    : run name (idempotent — returns existing run if name already exists)
    api_key : your API key (visible at the top-right of the dashboard)
    host    : server base URL (default http://localhost:5000)
    config  : optional dict of hyperparameters to store with the run
    """
    url     = f"{host.rstrip('/')}/api/v1/runs"
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    body: Dict[str, Any] = {'project': project, 'name': name}
    if config is not None:
        body['config'] = config

    r = requests.post(url, json=body, headers=headers, timeout=30)
    if not r.ok:
        err = r.json().get('error', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
        raise WandBError(f"init() failed ({r.status_code}): {err}")

    data = r.json()
    return Run(
        host=host,
        api_key=api_key,
        run_id=data['run_id'],
        project_id=data['project_id'],
    )
