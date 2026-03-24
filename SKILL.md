# SKILL.md
Patterns and gotchas specific to this codebase. Only things an agent would get wrong without them.

---

## Naming

- Python: `snake_case` functions/vars, `PascalCase` classes, `SCREAMING_SNAKE` constants
- JS: `snake_case` vars, `camelCase` methods, `PascalCase` components
- Booleans: `is_` / `has_` prefix, positive form (`is_loading`, not `is_not_loaded`)

---

## Flask

### Use per-route auth decorators, not `before_request`
`before_request` on a blueprint applies to every route and can't be selectively skipped — it silently double-runs auth logic when combined with decorators. Use `@api_key_required` / `@login_required` per route.

### Catch-all error handler must return JSON — never set `PROPAGATE_EXCEPTIONS = False`
`PROPAGATE_EXCEPTIONS = False` suppresses exceptions in pytest, hiding test failures silently. Register a catch-all handler in `create_app()` instead.

### SPA catch-all must abort on API prefixes
Without this, a mistyped `/api/v1/foo` returns HTML 200 and the caller's JSON parse fails silently:
```python
if path.startswith(('api/', 'auth/', 'files/', 'health')):
    abort(404)
```

### `get_json()` returns `None` on bad JSON — always check before field access
```python
data = request.get_json(force=True)
if not isinstance(data, dict):
    return err('Request body must be a JSON object', 400)
```

### WAL mode and foreign keys must be set on every new SQLite connection
They are not persisted to the DB file — `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` must be run on each new connection.

### Use `INSERT OR IGNORE` then SELECT — never SELECT-then-INSERT
SELECT-then-INSERT has a TOCTOU race under 4 concurrent workers: both pass the SELECT, both try INSERT, one gets a UNIQUE violation. `INSERT OR IGNORE` is atomic with respect to the constraint.

### Health endpoint must do a real DB query, not just return ok
A process-only health check passes load balancer checks even when the DB file is inaccessible. Always `SELECT 1` to verify connectivity.

---

## Auth & Security

### `/auth/login` serves the login page — `/auth/google` triggers the OAuth redirect
Three-stage flow: `/auth/login` → `login.html` (static) → user clicks button → `/auth/google` → Google consent → `/auth/callback`. The button must link to `/auth/google`, not `/auth/login` (that would loop).

### `/auth/me` must never use `@login_required`
It's called via `fetch()` on app startup. The decorator issues a 302 redirect to HTML, which breaks the JSON parse. Check the session manually and always return JSON with `logged_in: false`.

### `authlib` validates OAuth state automatically — don't implement a custom check
`authorize_access_token()` validates the `state` parameter. Do not skip or re-implement it.

### API key goes in the Authorization header only — never a query param
Query strings appear in server logs and browser history.

### Verify run ownership via JOIN through `projects` — never trust `run_id` alone
```sql
SELECT r.id FROM runs r
JOIN projects p ON p.id = r.project_id
WHERE r.id = ? AND p.user_id = ?
```
Return 404 (not 403) on mismatch — don't reveal whether the run exists.

### Return 409 from `/log` if the run is not `running`
Body: `{"error": "Run is terminated. Call /resume to continue logging."}`. Only `/resume` re-opens a run.

### Check `cursor.rowcount` after DELETE — never return silent 200
A 0-rowcount delete means the resource doesn't exist or belongs to another user. Return 404.

### Input limits: names ≤ 200 chars, step ≤ 10M, config ≤ 64 KB, images ≤ 20 per step as `{str: str}`

### Image validation must go size check → decode → PIL open — cheapest rejection first
1. `len(b64) > 27_000_000` → reject before any decode
2. `base64.b64decode(validate=True)` → reject invalid encoding
3. `Image.MAX_IMAGE_PIXELS = 50_000_000` then `Image.open()` → reject decompression bombs
4. `.convert('RGB')` → save

### Track saved image filenames and delete them if the step fails partway through
If any image save or the JSONL append fails, delete already-saved files before re-raising. Otherwise orphaned files accumulate with no DB reference.

### Apply `_safe_name()` to every user-supplied path component
Project name, run name, and metric key can contain `../`, `/`, or shell characters. Never use them raw as filesystem path components.

### Use `send_from_directory` for file serving — never manual path join + open
`send_from_directory` resolves the path and verifies it stays inside the base directory before serving, preventing path traversal.

### Delete run/project files from disk after the DB DELETE
DB cascade removes rows but not the `data/mltracker/` directories. Always `shutil.rmtree(..., ignore_errors=True)` after delete — otherwise EBS fills silently.

### flask-limiter must use Redis — in-memory storage is per-worker
With 4 Gunicorn workers, in-memory limits are 4× the configured value. Use `RATELIMIT_STORAGE_URI` pointing at Redis.

Rate-limit key is the raw Bearer token. Do NOT use `g.user_id` — the limiter's `before_request` hook runs before `@api_key_required` sets it.

Current limits: `POST /runs` 60/min · `POST /runs/<id>/log` 600/min · `POST /auth/regenerate-key` 5/hr.

### Every CDN resource needs a pinned version and SRI hash
`integrity="sha384-..."` + `crossorigin="anonymous"` on every CDN tag. Note: SRI inside `<script type="importmap">` only works Chrome 112+ / Firefox 126+.

### Store secrets in `/etc/mltracker.env` (mode 600) — never `/etc/environment`
`/etc/environment` is world-readable. Load via systemd `EnvironmentFile=`.

### Admin is checked at request time via `SELECT MIN(id) FROM users` — never cached in session
The session cookie persists across restarts; caching admin status there wouldn't update if users were deleted.

---

## Storage

### Scalar metrics and image refs are JSONL files, not SQLite rows
SQLite WAL fsyncs per transaction (~10–20ms on Windows). OS-buffered file append is ~200× faster. SQLite stores metadata only (users, projects, runs — never log data).

Line format: `{"step": 0, "ts": 1718000000, "loss": 0.5, "pred": {"type": "image", "name": "0_pred.png"}}`

### `_KEY_CACHE` in `auth.py` avoids a DB lookup on every API-key-authenticated request
Maps `api_key → user_id`. Must be evicted on `POST /auth/regenerate-key` via `invalidate_api_key(old_key)`.

### `_RUN_CACHE` in `routes/api.py` avoids the ownership JOIN on every `/log` call
Maps `(run_id, user_id) → run info dict`. Only `running` runs are cached. Evict on `/finish` and `/resume` via `_RUN_CACHE.pop((run_id, user_id), None)`. Store as plain `dict` — `sqlite3.Row` becomes invalid after the connection closes.

---

## Frontend

### Button hover is always `#2563eb` — never `--hover-bg`
`--hover-bg` is for tree-item row hover only. All buttons use the fixed blue.

### Use a string key in `watch()` when watching multiple reactive values — not an array
```js
// WRONG — new array reference every render, always triggers
watch(() => [proj?.id, run?.id], reset)
// CORRECT
watch(() => `${proj?.id ?? ''}_${run?.id ?? ''}`, reset)
```

### Guard `save_layout()` against empty card order
Selection change resets `card_order = []`, then the watcher fires immediately. Without `if (!card_order.value.length) return`, it overwrites the persisted layout with an empty one.

### Always clear the refresh timer on selection change and `onUnmounted`
Forgetting causes overlapping timers that escalate API request rate as the user clicks. Use `setTimeout` rescheduled each tick (not `setInterval`) — naturally applies variable delay.

### Auto-refresh uses exponential backoff on failures
`delay = Math.min(60000, 5000 * 2 ** fail_count)`. Reset to 5000 on success. Stop when `run.status !== 'running'`.

### `ImageSlider` receives `runs: [{label, images: [{step, url}]}]`
Project view: one card per image key; `runs` has one entry per project run, with `images: []` if that run has no data for this key. Single-run view: `runs` is a one-element array. Merged step list: `[...new Set(runs.flatMap(r => r.images.map(x => x.step)))].sort((a,b)=>a-b)`.

### localStorage keys: `wandb_layout_run_<id>`, `wandb_layout_proj_<id>`, `wandb_last_sel`

### Metric keys with `/` are grouped into a `MetricGroup` container at render time
`compute_units(card_order)` partitions flat keys by first path segment: `train/loss` and `train/acc` become a group with `unit_key = 'group::train'`. `card_order` stays flat (individual keys) — groups are derived, never stored. Group widths are stored in `card_sizes['group::train'] = {w}`. Child cards inside a group receive `width: null` (flex fills them evenly). Drag operates on unit keys, not flat keys — `start_drag` moves all keys of the dragged unit.

---

## SDK

### The background thread batches multiple queued steps into one POST
Single step: `{step, ts, loss: 0.3, ...}`. Multiple steps: `{"steps": [...]}`. Server handles both. Never send an empty `steps` list.

### Credentials priority: explicit arg → env var → `~/.mltracker` file → interactive prompt
Prompt saves to `~/.mltracker` (chmod 600). Env vars: `MLTRACKER_API_KEY`, `MLTRACKER_HOST`.

---

## Nginx

### Nginx location regex must use `(/|$)` — not a bare trailing slash
`location ~ ^/(api|auth|files|health)/` only matches paths that have a slash after the prefix. `/health` (no trailing slash) falls through to `try_files` and returns the SPA HTML instead of JSON. Always write `(/|$)`:
```nginx
location ~ ^/(api|auth|files|health)(/|$) {
```
This applies to the generated config in `bootstrap.sh` and the reference `nginx.conf`.

---

## Workflow

### Before every commit: update `CLAUDE.md` and `SKILL.md`, write a detailed commit message
