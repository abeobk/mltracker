# SKILL.md

## Code Naming Conventions

- Short but informative names; use common abbreviations (e.g. `str`, `req`, `resp`).
- **Python classes / JS classes**: PascalCase — `RunRecord`, `ChartPanel`
- **Python functions / methods**: snake_case — `get_run`, `log_metrics`
- **Python / JS local variables**: snake_case — `run_id`, `api_key`
- **JS class member variables**: `_snake_case` — `_charts`, `_sel_run`
- **JS class methods**: camelCase — `loadMetrics`, `renderCharts`
- **Constants**: SCREAMING_SNAKE — `DB_PATH`, `FILES_DIR`
- **Booleans**: `is_` / `has_` / `can_` / `should_` prefix, positive form — `is_loading`, `has_images`
- Short 0-line statements stay on same line: `if condition: return`
- Comment important/non-obvious code.

---

## Flask Patterns

### S-01 · App factory with blueprints
Always use the app-factory pattern so Gunicorn can import `create_app`:
```python
# app.py
def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(config or DefaultConfig)
    db.init_app(app)
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    return app
```
Gunicorn entry: `gunicorn "app:create_app()"`.

### S-02 · Use `g` for per-request state; enforce auth with a decorator, not `before_request`
Set authenticated user in a **decorator** and store in `flask.g`. Do NOT use `before_request`
on the blueprint — it applies to every route equally and cannot be selectively skipped.
A decorator gives explicit, per-route control and is easier to audit.
```python
from functools import wraps
def api_key_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing Bearer token'}), 401
        key = auth[7:].strip()
        row = get_db().execute(
            "SELECT id FROM users WHERE api_key = ?", (key,)).fetchone()
        if not row:
            return jsonify({'error': 'Invalid API key'}), 401
        g.user_id = row['id']
        return f(*args, **kwargs)
    return wrapper
```
Never combine `before_request` AND the decorator for the same auth — it runs the DB lookup twice.

### S-03 · Return consistent JSON error envelopes; register a JSON error handler
```python
def err(msg, code=400):
    return jsonify({'error': msg}), code
```
Never return HTML error pages from API routes. Do NOT set `PROPAGATE_EXCEPTIONS = False` —
that flag suppresses exceptions in pytest, silently hiding test failures.
Instead, register a catch-all error handler in `create_app()`:
```python
@app.errorhandler(Exception)
def handle_exception(e):
    code = getattr(e, 'code', 500)
    return jsonify({'error': str(e)}), code
```
This returns JSON for unhandled exceptions in production while letting pytest see them normally.

### S-04 · `teardown_appcontext` for DB connection cleanup
```python
@app.teardown_appcontext
def close_db(exc):
    db_conn = g.pop('db', None)
    if db_conn is not None:
        db_conn.close()
```

---

## SQLite Patterns

### S-05 · Always use parameterised queries — never string-format SQL
```python
# Correct:
cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
# Wrong — SQL injection risk:
cur.execute(f"SELECT * FROM runs WHERE id = {run_id}")
```

### S-06 · Enable WAL mode + foreign keys on every new connection
```python
def get_db():
    if 'db' not in g:
        conn = sqlite3.connect(current_app.config['DB_PATH'])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        g.db = conn
    return g.db
```
WAL allows concurrent reads during a write — critical for multi-run logging.

### S-07 · `sqlite3.Row` gives dict-like access without ORM overhead
```python
row = cur.fetchone()
run_id = row['id']   # column by name
```

### S-08 · Wrap batch inserts in a single transaction; use `INSERT OR IGNORE` for idempotent upserts
```python
with get_db() as conn:   # auto-commits or rolls back on exception
    conn.execute(
        "INSERT OR IGNORE INTO projects(user_id, name) VALUES (?, ?)",
        (user_id, name)
    )
```
One transaction for N rows is ~N× faster than N individual commits.
Use `INSERT OR IGNORE` for tables where the row may already exist and you don't want to overwrite it (e.g. project/run creation).
Scalar metrics and images are no longer stored in SQLite — they go to JSONL files (see S-52).

---

## Google OAuth (authlib)

### S-09 · Register OAuth client once at app-factory time
```python
from authlib.integrations.flask_client import OAuth
oauth = OAuth()

def create_app():
    app = Flask(__name__)
    oauth.init_app(app)
    oauth.register('google',
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'})
    ...
```

### S-10 · Always validate `state` on OAuth callback to prevent CSRF
`authlib` does this automatically when you use `authorize_redirect` + `authorize_access_token` — do NOT skip the `state` check or implement a custom callback flow.

### S-11 · Store minimal user info in Flask session — not the access token
```python
session['user'] = {
    'id': user_row['id'],
    'email': info['email'],
    'name':  info.get('name'),
    'picture': info.get('picture'),
}
```
Never store the Google access token in the session — it's not needed after the initial user lookup/creation.

---

## API Key Authentication

### S-12 · Generate API keys with `secrets.token_hex(32)`
```python
import secrets
api_key = secrets.token_hex(32)   # 64-char hex string, 256 bits of entropy
```
Store plain (not hashed) — API keys are designed to be sent as bearer tokens. Hashing would require hash-comparison on every request.

### S-13 · Check `Authorization: Bearer <key>` header, not query param
Never accept API keys in query strings — they end up in server logs and browser history.
```python
auth = request.headers.get('Authorization', '')
if not auth.startswith('Bearer '):
    return err('Missing Authorization header', 401)
key = auth[7:].strip()
```

---

## File Storage

### S-14 · Never use raw user-controlled strings as path components — sanitise first
Build storage paths from sanitised project name, run name, and key only.
Never use them raw — they are user-supplied strings that can contain `../`, `/`, or shell-special characters.
```python
def _safe_name(name: str) -> str:
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)

run_dir  = os.path.join(FILES_DIR, _safe_name(project_name), _safe_name(run_name))
filename = f"{step}_{_safe_name(key)}.png"
```
The sanitisation keeps alphanumeric, `-`, `_`, `.` and replaces everything else with `_`.
This eliminates path traversal via names/keys like `../../etc/passwd`.

### S-15 · Decode base64 images server-side — size check FIRST, then validate, then save
The function signature in `storage.py` takes the path components separately so that
sanitisation is always applied inside the function — never trust the caller to sanitise.
The order is: size check → decode (validate=True) → set pixel cap → PIL validate → save.
```python
import base64, io, os
from PIL import Image
from flask import current_app

MAX_B64_BYTES = 27_000_000   # ~20 MB decoded

def save_image(data_b64: str, project_name: str, run_name: str, step: int, key: str) -> str:
    """Validate, decode, save image. Returns filename only (e.g. '5_pred.png')."""
    if len(data_b64) > MAX_B64_BYTES:
        raise ValueError("Image payload too large")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise ValueError("Invalid base64 encoding")
    Image.MAX_IMAGE_PIXELS = 50_000_000
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Image.DecompressionBombError:
        raise ValueError("Image dimensions too large")
    img = img.convert('RGB')
    safe_key   = _safe_name(key)
    filename   = f"{step}_{safe_key}.png"
    images_dir = os.path.join(_run_dir(project_name, run_name), 'images')
    os.makedirs(images_dir, exist_ok=True)
    img.save(os.path.join(images_dir, filename), format='PNG')
    return filename   # stored in JSONL; URL built by route handler
```
Use `current_app.config['FILES_DIR']` — never a module-level constant — because the path
is set in the app factory and is only available inside a request context.

### S-16 · Serve stored files via a dedicated `/files/<path>` route
```python
@app.route('/files/<path:rel_path>')
@login_required
def serve_file(rel_path):
    return send_from_directory(current_app.config['FILES_DIR'], rel_path)
```
Use `current_app.config['FILES_DIR']` — not a module-level constant — because the path
is configured in the app factory and is only available inside a request context.
`send_from_directory` protects against path-traversal attacks (it checks the resolved
path is still inside the base directory before serving).

---

## Chart.js in Vue 3

### S-17 · Destroy chart before re-creating on data change
Chart.js instances are not reactive. When the dataset changes (e.g. different run selected), destroy the old instance first:
```js
if (_chart) { _chart.destroy(); _chart = null; }
_chart = new Chart(canvas, config);
```

### S-18 · Pass Chart.js colours as CSS variable values, not hard-coded hex
Read CSS custom properties at chart-create time so dark/light theme works:
```js
const style = getComputedStyle(document.documentElement);
const grid_color = style.getPropertyValue('--border').trim();
```

### S-19 · Use `animation: false` for live-updating charts (streaming logs)
```js
{ options: { animation: false, responsive: true, maintainAspectRatio: false } }
```
This prevents the chart from re-animating on every data append.

---

## Vue 3 + REST API Patterns

### S-20 · Use `watchEffect` / `watch` to reload data when selection changes
```js
watch(() => sel_run_id.value, async (id) => {
    if (!id) return;
    metrics.value = await fetchMetrics(id);
});
```

### S-21 · Debounce rapid tree clicks before firing API requests
If a user clicks through the tree quickly, debounce 150 ms to avoid firing many API calls:
```js
let _load_timer = null;
function selectRun(id) {
    clearTimeout(_load_timer);
    _load_timer = setTimeout(() => loadRunData(id), 150);
}
```

### S-22 · Button hover — fixed blue, not theme variable
```css
button:hover { background: #2563eb; border-color: #2563eb; color: #fff; }
```
`--hover-bg` is for tree-item rows only.

### S-23 · Expose `window.__app` after Vue mount for external callers
```js
const app = createApp(App);
app.mount('#app');
window.__app = { reload };   // expose only what's needed
```

---

## Gunicorn / Production Deployment

### S-24 · `sync` worker is safe for SQLite; avoid `gevent`/`eventlet`
SQLite's WAL mode supports concurrent reads, but a single write lock still exists. Sync workers (one request at a time per worker) avoid write-lock contention. For high write throughput, migrate to PostgreSQL.

### S-25 · Set `SECRET_KEY` from environment, never hard-code
```python
class Config:
    SECRET_KEY = os.environ['SECRET_KEY']   # raise at startup if missing
```
On EC2: store in a dedicated secrets file (see S-39) or AWS Secrets Manager.
Do NOT use `/etc/environment` — it is world-readable. S-39 is the authoritative guide.

### S-26 · Serve static frontend via Flask in dev; Nginx in prod
Dev:
```python
app = Flask(__name__, static_folder='../frontend', static_url_path='')
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')
```
Prod: Nginx serves `frontend/` directly; Flask/Gunicorn handles `/api/*` and `/auth/*` only.

---

## CSS Layout Patterns

### S-27 · Drag-resizable left panel via CSS custom property
```css
#app {
    grid-template-columns: var(--left-w) 5px 1fr;
}
```
```js
function startResize(e) {
    const start_x = e.clientX;
    const start_w = parseInt(getComputedStyle(document.documentElement)
                    .getPropertyValue('--left-w'));
    const move = (e) => {
        const w = Math.max(160, Math.min(480, start_w + e.clientX - start_x));
        document.documentElement.style.setProperty('--left-w', w + 'px');
    };
    const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
}
```

---

## Workflow & Project Conventions

### S-28 · Commit checklist — docs must stay current
Before every `git commit`, update as needed:
1. `PROGRAM.md` — evolve the spec to match the current implementation
2. `SKILL.md` — add any new pattern or gotcha learned

### S-29 · User decisions override original spec
Record any deliberate deviation in `PROGRAM.md` so future sessions don't re-introduce removed features.

### S-30 · File locations
| File | Purpose |
|------|---------|
| `PROGRAM.md` | Authoritative spec and architecture |
| `PLAN.md` | Implementation, test, and deployment plan |
| `SKILL.md` | Patterns, gotchas, and knowhow |

---

## Run Ownership & Lifecycle

### S-32 · Every write endpoint must verify run ownership via JOIN
Never trust the `run_id` URL parameter alone. Always verify ownership by joining through
`projects` to check `user_id`:
```
SELECT r.id, r.project_id, r.status
FROM runs r
JOIN projects p ON p.id = r.project_id
WHERE r.id = ? AND p.user_id = ?
```
If no row is returned, respond with 404 — do not reveal whether the run exists at all.
Apply this check on `/log`, `/finish`, and `/resume`. Skipping it allows any authenticated
user to modify another user's runs.

### S-33 · Status gate — reject `/log` if run is not `running`
After verifying ownership, check `run.status` before accepting log data:
- If `status` is `finished` or `crashed`: return 409 Conflict
- Response body: `{"error": "Run is terminated. Call /resume to continue logging."}`
- The caller must explicitly POST to `/resume` before logging is accepted again
This prevents stale training scripts from polluting a completed run's data.

### S-34 · Resume endpoint — only re-opens terminated runs
`POST /api/v1/runs/<run_id>/resume`:
1. Verify ownership (S-32 JOIN check)
2. If already `running`: return 409 (already active — do not double-resume)
3. Set `status = 'running'`, clear `finished_at`
4. Do NOT delete existing metrics/images — resume continues from where the run left off
5. Return `{"ok": true, "run_id": run_id}`

---

## Safe DB Patterns

### S-35 · TOCTOU-safe upsert — use `INSERT OR IGNORE`, not SELECT-then-INSERT
A SELECT followed by a conditional INSERT has a race condition under concurrent workers:
both workers pass the SELECT, then both try to INSERT, and one gets a UNIQUE constraint error.
Instead:
```
INSERT OR IGNORE INTO projects(user_id, name) VALUES (?, ?)
SELECT id FROM projects WHERE user_id = ? AND name = ?
```
The INSERT is atomic with respect to the UNIQUE constraint. Always follow with a SELECT
to retrieve the id regardless of whether the insert happened.

### S-36 · Schema foreign keys must use `ON DELETE CASCADE`
Declare all FK relationships with cascade so that deleting a parent automatically cleans
up children:
```
project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE
run_id     INTEGER NOT NULL REFERENCES runs(id)     ON DELETE CASCADE
```
Without CASCADE, deleting a project that has runs raises a FK constraint error
(when `PRAGMA foreign_keys=ON`). Manual multi-table deletes are error-prone — use CASCADE.

---

## Configuration & Secrets

### S-38 · Read boolean config values from environment variables
Never hardcode `True`/`False` for security-sensitive settings like `SESSION_COOKIE_SECURE`.
Parse from env:
```python
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
```
The production environment file sets `SESSION_COOKIE_SECURE=true`. If hardcoded to `False`,
session cookies are sent over plain HTTP even in production.

### S-39 · Store secrets in a dedicated env file, not `/etc/environment`
`/etc/environment` is world-readable by default. Instead:
- Create `/etc/mltracker.env` owned by root, mode 600
- Reference it in the systemd unit via `EnvironmentFile=/etc/mltracker.env`
- Only root and the service process can read it

---

## Image Upload Safety

### S-40 · Check payload size before decoding base64 — see S-15 for the full pattern
Always reject oversized base64 payloads BEFORE calling `base64.b64decode`.
A malicious client can send repeated max-size payloads that block all Gunicorn workers
on CPU-bound PIL decoding. Reject immediately if `len(data_b64) > MAX_B64_BYTES`.
The complete safe ordering is in S-15: size check → decode → PIL validate → save.

---

## API Safety

### S-46 · `/auth/me` must return JSON `logged_in:false` — never use `@login_required` on it
The frontend calls `GET /auth/me` as a `fetch()` to determine if the user is logged in.
If `@login_required` is applied, an unauthenticated request receives a `302` redirect to
`/auth/login`. The browser follows the redirect, gets an HTML page, and the JSON parse fails.
Instead, check the session manually and always return JSON:
```python
@auth_bp.get('/me')
def me():
    if 'user' not in session:
        return jsonify({'logged_in': False})
    # ... fetch api_key from DB ...
    return jsonify({**session['user'], 'api_key': row['api_key'], 'logged_in': True})
```
This is the only auth endpoint that must NOT use `@login_required`.

### S-47 · Roll back saved image files if a batch log fails partway through
When logging multiple images in one step, save each to disk and track the filenames.
If any later save (or the JSONL append) fails, delete the successfully-saved files:
```python
saved_filenames = []
images_dir = os.path.join(_run_dir(project_name, run_name), 'images')
try:
    for key, b64 in images_raw.items():
        filename = save_image(b64, project_name, run_name, step, key)
        saved_filenames.append(filename)
    append_metrics(project_name, run_name, step, scalars, image_refs, ts)
except Exception:
    for fname in saved_filenames:
        path = os.path.join(images_dir, fname)
        if os.path.exists(path):
            os.remove(path)
    raise
```
Without this, a partial failure leaves orphaned image files on disk that are never cleaned up.

### S-48 · Delete run/project files from disk when deleting via API
The DB cascade removes run/project rows, but the `data/mltracker/` directories remain.
After the DB DELETE, call the storage helpers:
```python
delete_run_files(project_name, run_name)      # shutil.rmtree(run_dir)
delete_project_files(project_name)            # shutil.rmtree(proj_dir)
```
Paths are built from sanitised names via `_safe_name()`, not integer IDs.
`ignore_errors=True` prevents failures if the directory never existed (e.g. a run with no logs).

### S-42 · Return 404 (not silent 200) when a DELETE targets a non-owned resource
When deleting a project or run, the WHERE clause includes `AND user_id = ?` (or the
project ownership JOIN). If no row matches, SQLite deletes 0 rows and returns no error.
The handler MUST check `rowcount` after the DELETE:
- If `cursor.rowcount == 0`: the resource does not exist or belongs to someone else → return 404
- Do NOT return `{"ok": true}` for a no-op delete — this masks ownership bypass attempts

### S-43 · Validate step range and config size on every /log and /runs call
- `step`: must be a non-negative integer, maximum 10,000,000. Reject with 400 otherwise.
  An uncapped step value produces absurdly long filenames and wastes DB storage.
- `config` blob: must be ≤ 64 KB when serialised. Reject with 400 if larger.
  An uncapped config allows any authenticated user to inflate the database arbitrarily.

### S-49 · Always validate `get_json()` returns a dict before accessing fields
`request.get_json(force=True)` returns `None` if the body is not valid JSON (default
`silent=True`). Calling `data.get(...)` on `None` raises `AttributeError`, Flask returns
an HTML 500 traceback. Always check immediately:
```python
data = request.get_json(force=True)
if not isinstance(data, dict):
    return err('Request body must be a JSON object', 400)
```
Also validate that `images` (if present) is a dict of `{str: str}`:
```python
images = data.get('images', {})
if not isinstance(images, dict):
    return err('images must be a JSON object', 400)
if len(images) > 20:
    return err('Too many images per step (max 20)', 400)
```
The per-step image cap prevents DoS via hundreds of images in one log call.

### S-44 · Add rate limiting on API key endpoints and /log — use Redis backend
Without rate limiting:
- The `api_key_required` DB lookup can be hammered to enumerate valid API keys
- The `/log` endpoint can be flooded to fill disk and DB with junk data
Recommended approach: use `flask-limiter` with a **Redis** backend.
Apply at minimum:
- `POST /api/v1/runs/<id>/log` — e.g. 60 requests/minute per API key
- `POST /api/v1/runs` — e.g. 30 requests/minute per API key
- `POST /auth/regenerate-key` — e.g. 5 requests/minute per session

**CRITICAL: do NOT use the default in-memory (`MemoryStorage`) backend with multiple workers.**
Each Gunicorn worker maintains its own counter independently. With 4 workers, the effective
limit is 4× the configured value — the rate limiter provides no protection. Use Redis:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(key_func=get_remote_address,
                  storage_uri="redis://localhost:6379")
```
For API key rate limiting, use a custom `key_func` that returns `g.user_id` (set by the
`api_key_required` decorator) so the limit is per-user, not per-IP.

### S-45 · Add Subresource Integrity (SRI) hashes to all CDN script and link tags
Any CDN resource loaded without an `integrity=` attribute is a supply-chain attack surface.
If unpkg.com or cdnjs is compromised or serves a malicious version, the attacker gets full
access to the user's session and API key. Always pin CDN resources with SRI:
```html
<script src="https://unpkg.com/vue@3.x.x/dist/vue.esm-browser.prod.js"
        integrity="sha384-<hash>"
        crossorigin="anonymous"></script>
```
Generate the hash with: `curl -s <url> | openssl dgst -sha384 -binary | openssl base64 -A`
Note: SRI requires the CDN to serve the file with `Access-Control-Allow-Origin: *` (unpkg does).

**Import map SRI browser support caveat:**
SRI `integrity` attributes on module specifiers inside `<script type="importmap">` have
limited support: Chrome 112+, Firefox 126+. Older browsers silently skip the check and load
the module WITHOUT verifying the hash. `<link>` and `<script>` tags outside the import map
have full SRI support everywhere. Document minimum supported browser versions and consider
a fallback warning for users on old browsers.

---

## Frontend Patterns

### S-50 · SPA catch-all route must guard API prefixes to prevent HTML masking JSON 404s
The Flask catch-all that serves `index.html` for unknown SPA routes will intercept
unregistered API paths (e.g. `/api/v1/typo`) and return HTML 200 instead of JSON 404.
This silently breaks API clients — they receive HTML, JSON parse fails, and the real error
(wrong endpoint) is hidden. Always guard:
```python
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def spa(path):
    if path.startswith(('api/', 'auth/', 'files/', 'health')):
        abort(404)
    return send_from_directory(app.static_folder, 'index.html')
```
Register this route LAST in the app factory, after all blueprints.

---

### S-51 · Health endpoint should verify DB connectivity, not just process liveness
A health endpoint that returns `{"status": "ok"}` after process startup passes load-balancer
health checks even when the DB file is inaccessible (unmounted EBS, permissions changed).
All real requests then fail, but health checks keep routing traffic to the broken instance.
Add a lightweight DB ping:
```python
@app.get('/health')
def health():
    try:
        get_db().execute("SELECT 1")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500
```

---

### S-52 · Store scalar metrics and image refs as append-only JSONL, not SQLite rows
SQLite WAL commits fsync to disk on every transaction. On Windows this is ~10-20ms per commit.
A run logging 200 steps × 1 commit = 2-4 seconds of wall time just on disk syncs.

Instead, write each log step as one JSON line appended to a per-run file:
```
data/mltracker/<project_name>/<run_name>/metrics.jsonl
```
(Names are sanitised with `_safe_name()` — alphanumeric, `-`, `_`, `.` only.)

Each line is a flat JSON object with step, optional ts, scalars, and image refs:
```json
{"step": 0, "ts": 1718000000.1, "loss": 0.50, "acc": 0.70}
{"step": 1, "ts": 1718000005.3, "loss": 0.40, "pred": {"type": "image", "name": "1_pred.png"}}
```
`open(file, 'a') + write()` uses OS write buffering — no fsync per append.
This is ~200× faster than SQLite WAL per step on Windows.

**SQLite is used for:** users, projects, runs (metadata only — no data written during log()).
**JSONL is used for:** scalar metrics AND image references (inline in same line as scalars).
**Images** are saved to `images/<step>_<key>.png`; only the filename is stored in JSONL.
**Cleanup:** `shutil.rmtree(run_dir)` removes `metrics.jsonl` and `images/` together.

```python
import json, os

def append_metrics(project_name, run_name, step, scalars,
                   image_refs=None, ts=None):
    row = {'step': step}
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
```

Querying: read all rows, skip `'step'` and `'ts'` keys, build per-key series in Python, apply downsampling (every Nth point). Image keys are those whose value is `{"type": "image", ...}`.

---

### S-53 · commit=False buffering + async posting in SDK (matches WandB pattern)
WandB lets users accumulate data across multiple `log()` calls within one step:
```python
run.log({"loss": loss}, commit=False)   # buffer — don't send yet
run.log({"acc": acc},   commit=False)   # buffer more
run.log({"img": img})                   # commit=True (default) — flush all + send
```

`log()` returns immediately — the actual HTTP POST happens in a background thread via a `queue.Queue`. The background thread greedily drains the queue and batches multiple steps into one POST when possible (see S-56).

```python
def log(self, data, step=None, commit=True):
    self._buffer.update(data)
    if not commit:
        return  # accumulate only — no network call

    ts = time.time()   # timestamp at commit moment
    if step is None:
        step = self._step; self._step += 1
    else:
        self._step = step + 1

    payload = {'step': step, 'ts': ts}
    images  = {}
    for key, value in self._buffer.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            payload[key] = value
        else:
            images[key] = self._encode_image(value)
    self._buffer.clear()
    if images:
        payload['images'] = images
    self._queue.put(payload)   # non-blocking — worker handles POST

def finish(self, status='finished'):
    if self._buffer:
        self.log({})          # force flush uncommitted data
    self._queue.put(None)     # shutdown sentinel
    self._worker.join()       # wait for all POSTs to complete
    if self._worker_error:
        raise self._worker_error
    self._post(f'/api/v1/runs/{self._run_id}/finish', {'status': status})
```

**Key rules:**
- `commit=False` does NOT advance the step counter or timestamp
- Buffer is cleared on every `commit=True` flush
- `self._buffer` initialised to `{}`, `self._queue = queue.Queue()`, daemon thread started in `__init__`
- `finish()` MUST be called to guarantee all steps are delivered

---

### S-54 · In-memory caches eliminate SQLite calls on the hot log() path
Even after removing all SQLite writes from `log()`, the `get_db()` call still creates a new
SQLite connection per request (Flask `g` is per-request), which costs ~5–10ms on Windows even
for a read-only query (connection setup + `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`).

Fix with two in-memory dicts that persist across requests:

**Auth cache** (`auth.py`):
```python
_KEY_CACHE: dict[str, int] = {}   # api_key → user_id

def api_key_required(f):
    def wrapper(*args, **kwargs):
        key = request.headers.get('Authorization', '')[7:].strip()
        user_id = _KEY_CACHE.get(key)
        if user_id is None:
            row = get_db().execute("SELECT id FROM users WHERE api_key=?", (key,)).fetchone()
            if not row: return jsonify({'error':'Invalid API key'}), 401
            user_id = row['id']
            _KEY_CACHE[key] = user_id
        g.user_id = user_id
        return f(*args, **kwargs)
    return wrapper

def invalidate_api_key(api_key: str):
    _KEY_CACHE.pop(api_key, None)  # call this from regenerate-key route
```

**Run cache** (`routes/api.py`):
```python
_RUN_CACHE: dict[tuple, dict] = {}   # (run_id, user_id) → run_info dict

def _get_owned_run(run_id, user_id):
    cached = _RUN_CACHE.get((run_id, user_id))
    if cached: return cached
    row = get_db().execute("""
        SELECT r.id, r.project_id, r.status, r.name AS run_name, p.name AS project_name
        FROM runs r JOIN projects p ON p.id = r.project_id
        WHERE r.id = ? AND p.user_id = ?""", (run_id, user_id)).fetchone()
    if row and row['status'] == 'running':
        _RUN_CACHE[(run_id, user_id)] = dict(row)   # store as plain dict — Row invalid after close
    return row

# Evict from cache on finish/resume so next call re-checks status:
_RUN_CACHE.pop((run_id, g.user_id), None)
```

After cache warm-up, the hot `log()` path makes zero SQLite calls. Only `running` runs are cached — `finish`/`resume` evict the entry.

---

### S-55 · `wandb.Image` client wrapper for logging images (including OpenCV BGR arrays)
```python
class Image:
    """Wrap a numpy array or PIL Image for logging.

    Args:
        data:  numpy array (H×W×3 uint8) or PIL Image
        bgr:   True for OpenCV/cv2 arrays — channels are flipped BGR→RGB before encoding
        caption: optional string (reserved for future display)
    """
    def __init__(self, data, bgr=False, caption=''):
        self.data    = data
        self.bgr     = bgr
        self.caption = caption
```

In `Run._encode_image()`, unwrap `wandb.Image` first:
```python
if isinstance(value, Image):
    raw = value.data
    try:
        import numpy as np
        if isinstance(raw, np.ndarray):
            if value.bgr and raw.ndim == 3 and raw.shape[2] == 3:
                raw = raw[..., ::-1]   # BGR → RGB (view, no copy needed)
            from PIL import Image as PILImage
            raw = PILImage.fromarray(raw.astype('uint8'))
    except ImportError:
        pass
    value = raw
# ... then fall through to the existing PIL/numpy encoding path
```

The BGR flip is a numpy advanced index — `raw[..., ::-1]` reverses channel order in-place (view).

---

### S-56 · Batch POST format — bundle multiple steps into one HTTP request
When `log()` is called faster than the server responds, the background thread's queue
accumulates multiple payloads. The worker drains them greedily and sends them in one POST:

**SDK worker:**
```python
def _post_worker(self):
    while True:
        item = self._queue.get()           # block until first item
        if item is None: return            # shutdown sentinel
        batch = [item]
        while True:                        # drain what's already queued
            try:
                extra = self._queue.get_nowait()
                if extra is None:
                    self._flush_batch(batch); return
                batch.append(extra)
            except queue.Empty:
                break
        self._flush_batch(batch)

def _flush_batch(self, batch):
    body = batch[0] if len(batch) == 1 else {'steps': batch}
    result = self._post(f'/api/v1/runs/{self._run_id}/log', body)
    if not result.get('ok'):
        self._worker_error = WandBError(f"log() failed: {result}")
```

**Server handler:**
```python
if 'steps' in data:
    for step_data in data['steps']:
        err = _log_single_step(run, step_data)
        if err: return err
else:
    err = _log_single_step(run, data)
    if err: return err
return jsonify({'ok': True})
```

Single-step format: `{step, ts, loss: 0.3, images: {...}}`
Batch format: `{steps: [{step, ts, ...}, {step, ts, ...}, ...]}`

---

### S-57 · Draggable + resizable dashboard cards with persistent layout

Each metric/image card is wrapped in a `DashCard` that supports drag-reorder and corner-resize.

**Drag reorder:**
```js
function start_drag(key) {
  dragging_key.value = key;
  document.body.style.userSelect = 'none';
  const on_up = () => {
    if (drag_over_key.value && drag_over_key.value !== dragging_key.value) {
      const arr = [...card_order.value];
      const from = arr.indexOf(dragging_key.value);
      const to   = arr.indexOf(drag_over_key.value);
      arr.splice(from, 1); arr.splice(to, 0, dragging_key.value);
      card_order.value = arr;
    }
    dragging_key.value = drag_over_key.value = null;
    document.body.style.userSelect = '';
    window.removeEventListener('mouseup', on_up);
  };
  window.addEventListener('mouseup', on_up);
}
```

**Corner resize (inside DashCard):**
```js
const on_move = ev => emit('resize', {
  w: Math.max(280, start_w + ev.clientX - start_x),
  h: Math.max(150, start_h + ev.clientY - start_y),
});
```

**localStorage persistence:**
```js
// Key by selection — run takes priority over project
function lstore_key() {
  if (sel_run?.id)     return `wandb_layout_run_${sel_run.id}`;
  if (sel_project?.id) return `wandb_layout_proj_${sel_project.id}`;
  return null;
}

function save_layout() {
  const k = lstore_key();
  if (!k || !card_order.value.length) return;  // guard against empty transitional state
  localStorage.setItem(k, JSON.stringify({ order: card_order.value, sizes: card_sizes.value }));
}
```

**Watcher pitfall — always use a string key, not an array:**
```js
// WRONG: new array every render, always triggers
watch(() => [proj?.id, run?.id], reset);

// CORRECT: string comparison is by value
watch(() => `${proj?.id ?? ''}_${run?.id ?? ''}`, reset);
```

### S-58 · Multi-run image cards — `image_cards` format

`ImageSlider` takes `runs: [{label, images: [{step, url}]}]` — not a flat `images` array.

**Project view:** one card per image key; `runs` has one entry per run (empty `images` array if that run has no data for this key — shows "No images" placeholder).

**Run view:** same format but `runs` is a single-element array.

**Merged step navigation:** `[...new Set(runs.flatMap(r => r.images.map(x => x.step)))].sort((a,b)=>a-b)`

**CSS grid for multi-run:**
```css
.img-grid-multi {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 6px;
  overflow-y: auto;
  align-content: start;
}
/* Single-run fills the full card body */
.img-grid-single { flex: 1; min-height: 0; display: flex; }
.img-grid-single .img-run-cell { flex: 1; border: none; background: none; }
.img-grid-single .img-run-cell img { flex: 1; height: 100%; aspect-ratio: unset; }
```

Each run cell: framed with `var(--panel2)` background + `var(--border)` border; run name header on top with `border-bottom`.

### S-41 · Always clear auto-refresh interval before setting a new one; add error backoff
Store the interval ID in a variable. Call `clearInterval` every time the selection changes
(different run selected, project selected, or nothing selected) and when the component unmounts.
Failing to do so causes multiple overlapping intervals to accumulate, creating an escalating
API request storm as the user clicks through the tree.

Also add exponential backoff on consecutive fetch failures — do not hammer a down server at
the full 5-second rate. Pattern:
```js
let _refresh_delay = 5000;
let _fail_count = 0;

async function refresh() {
    try {
        await loadRunData(_sel_run_id);
        _fail_count = 0;
        _refresh_delay = 5000;          // reset on success
    } catch (e) {
        _fail_count++;
        _refresh_delay = Math.min(60000, 5000 * Math.pow(2, _fail_count));
    }
    if (_sel_run && _sel_run.status === 'running') {
        _refresh_timer = setTimeout(refresh, _refresh_delay);
    }
}
```
Use `setTimeout` (rescheduled on each tick) rather than `setInterval` — this naturally
applies the variable delay and avoids the interval accumulation problem entirely.

---

### S-59 · First-user-is-admin pattern — check at request time, never store in session

Admin status is determined dynamically: the user whose `id = MIN(id)` in the `users` table is admin. Never cache this in the session cookie — the cookie persists across restarts and wouldn't update if users were deleted.

**`admin_required` decorator:**
```python
def _is_admin(user_id: int) -> bool:
    row = get_db().execute("SELECT MIN(id) AS min_id FROM users").fetchone()
    return row and row['min_id'] == user_id

def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not _is_admin(session['user']['id']):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper
```

**`/auth/me`** includes `is_admin: bool` so the frontend can conditionally render admin UI on startup — computed fresh on each `/me` call.

**Admin stats query** — one round-trip for all users:
```sql
SELECT u.id, u.email, u.name, u.picture, u.created_at,
       COUNT(DISTINCT p.id)  AS project_count,
       COUNT(DISTINCT r.id)  AS run_count,
       COALESCE(SUM(CASE WHEN r.finished_at IS NOT NULL
                         THEN r.finished_at - r.created_at ELSE 0 END), 0) AS total_run_seconds,
       MAX(r.created_at)     AS last_active
FROM users u
LEFT JOIN projects p ON p.user_id = u.id
LEFT JOIN runs r     ON r.project_id = p.id
GROUP BY u.id ORDER BY u.id
```

**Frontend:** `TopBar` shows `fa-users-gear` only when `user.is_admin`. Toggling sets `admin_view` ref; when true, `AdminPanel` replaces `LeftPanel + MainPanel` in the render tree.
