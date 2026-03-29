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

### Admin is checked at request time via `SELECT MIN(id) FROM users WHERE status='active'` — never cached in session
The session cookie persists across restarts; caching admin status there wouldn't update if users were deleted. The `WHERE status='active'` filter is required — otherwise a pending user with a low id would appear as admin.

### Password hashing uses `hashlib.scrypt` (stdlib) — never SHA256
SHA256 is a fast hash; attackers can try billions of guesses/second with a GPU. `hashlib.scrypt` is memory-hard (n=16384, r=8, p=1). Hash format: `<salt_hex>:<dk_hex>` where salt is 16 random bytes. Always run the scrypt computation even when the user lookup fails (use a dummy hash) to prevent timing-based user enumeration.

### First Google OAuth user auto-activates — email/password users always need approval
`POST /auth/register` always inserts with `status='pending_approval'`. `/auth/callback` checks `COUNT(*) FROM users` before insert: if 0, uses `status='active'`. This guarantees the admin bootstraps via OAuth. The `INSERT OR IGNORE` never downgrades an existing user's status.

### `api_key_required` must reject non-active users
Add `AND status = 'active'` to the key lookup query. Without it, a pending or deleted user with a known API key could still call write endpoints.

### Serve static auth pages via blueprint routes, not the SPA catch-all
`register.html` and `pending.html` are served by `GET /auth/register` and `GET /auth/pending` in `auth.py` using `send_from_directory`. The SPA catch-all only needs to handle `login.html` explicitly — all other `/auth/*` paths hit the blueprint first.

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

### Project dashboard needs its own refresh path — run refresh won't cover it
`_do_select_run` starts `_refresh_run_id`. `_do_select_project` starts `_refresh_proj_id`. They are mutually exclusive. Project refresh reloads `proj.runs` each tick (to catch status changes) then calls `load_project_dash`. Without this, a project view never updates while runs are active.

### `main-panel` needs `overflow: auto` (both axes) and `cards-grid` needs `min-width: min-content`
`overflow-y: auto` alone clips groups that grow wider than the panel — no horizontal scroll appears. `min-width: min-content` on `.cards-grid` lets the flex container expand beyond the panel so oversized groups are scrollable, not clipped.

### Run colors must use the run's project index, not the dataset array index
In project view, some metrics only exist for a subset of runs — the dataset array is shorter than `proj.runs`. Using the dataset index for color causes the same run to appear in different colors across different metric charts. Always assign `color: run_color(run_idx)` where `run_idx` is the run's position in `proj.runs`, then store it on the dataset object (`ds.color`) so MetricChart uses it directly.

### `run_color` reads the current theme at call time — call it when building the dataset, not at chart-render time
Chart.js reads colors at chart-create time. If `run_color` read the theme at that point it would be fine, but assigning the color when building the dataset (`load_project_dash`) ensures it matches the LeftPanel run-name colors which are computed at Vue render time.

### Chart.js animation should always be disabled — it fires on every data update
`animation: { duration: 300 }` re-animates on every refresh tick, making charts visually noisy. Set `animation: false` unconditionally.

### Card collapse state lives in `card_sizes[key].collapsed` — no separate store needed
`toggle_collapse(key)` spreads the existing size entry and flips `collapsed`. `save_layout` persists it automatically since it serialises all of `card_sizes`. Body and resize handle are conditionally rendered with `!props.collapsed`.

### Always clear the refresh timer on selection change and `onUnmounted`
Forgetting causes overlapping timers that escalate API request rate as the user clicks. Use `setTimeout` rescheduled each tick (not `setInterval`) — naturally applies variable delay.

### Auto-refresh uses exponential backoff on failures
`delay = Math.min(60000, 5000 * 2 ** fail_count)`. Reset to 5000 on success. Stop when `run.status !== 'running'`.

### `ImageSlider` receives `runs: [{label, images: [{step, url}]}]`
Project view: one card per image key; `runs` has one entry per project run, with `images: []` if that run has no data for this key. Single-run view: `runs` is a one-element array. Merged step list: `[...new Set(runs.flatMap(r => r.images.map(x => x.step)))].sort((a,b)=>a-b)`.

### localStorage keys: `wandb_layout_run_<id>`, `wandb_layout_proj_<id>`, `wandb_last_sel`

### Metric keys with `/` are grouped into a `MetricGroup` container at render time
`compute_units(card_order)` partitions flat keys by first path segment. `card_order` stays flat — groups are derived, never stored. Group size: `card_sizes['group::prefix'] = {w, h, collapsed?}`. Each child has its own `card_sizes[key]`. Two independent drag systems: `dragging_key`/`drag_over_key` for top-level unit reorder; `dragging_child_key`/`drag_over_child_key` for within-group reorder. Collapsed children are sorted to the end of the group's key list at render time so they appear at the bottom as compact strips.

### Group sizes must be explicitly restored from `saved.sizes` — they are not in `card_order`
`load_layout` rebuilds `card_sizes` by iterating `saved.order` (flat keys only). `group::prefix` entries in `saved.sizes` are skipped unless you add a second pass: `for (const [k,v] of Object.entries(saved.sizes)) { if (k.startsWith('group::')) merged[k] = v; }`.

### Group children should use `flex: 0 1 auto` so they shrink when the group is narrower
`flex: 0 0 auto` (no shrink) causes children to overflow the group. With `flex: 0 1 auto`, the inline `width` property acts as the flex-basis and children shrink proportionally when the group width forces it, stopping at `min-width: 200px`.

### Group layout mode controls child `{w, h}` — don't use individual csizes for grid/horizontal
In `grid` and `horizontal` modes, all children must share the group's height (uniform rows). Pass `group_sizes.h` as `child_h` — not `csizes.h`. In `vertical` and `grid` modes, children fill the column width via CSS; pass `null` as `child_w` so DashCard omits the inline width style. The layout is stored in `card_sizes['group::prefix'].layout`; default is `'grid'` (set in `default_size`). When the user switches layout the old individual sizes are preserved so switching back to `free` mode restores them.

### `display: grid` on `.metric-group-body.layout-grid` overrides the default flex — reset `flex` on children
The base `.metric-group-body .dashboard-card` rule sets `flex: 0 1 auto`. When the body switches to `display: grid`, the `flex` property has no meaning but can conflict with `width: auto !important`. Add `.layout-grid .dashboard-card { flex: unset; }` to avoid that.

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

### Bump `?v=N` in `index.html` on every commit that changes `app.js` or `style.css`
`style.css?v=N` and `app.js?v=N` are the cache-busting query strings. Increment N by 1 whenever either file changes — browsers cache aggressively and users won't see updates otherwise.
