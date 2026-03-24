# CLAUDE.md — MLTracker

<!-- =================================================================
     PROTECTED — DO NOT EDIT THIS SECTION WITHOUT EXPLICIT USER APPROVAL
     These are the agent workflow rules and must be preserved exactly.
     ================================================================= -->

## Agent Workflow Rules

- Read **SKILL.md** before starting any task.
- Develop knowhow continuously — write new patterns and gotchas to **SKILL.md**.
- **Before every `git commit`** — update `CLAUDE.md` and `SKILL.md` to reflect the current state.
- Write detailed commit messages explaining what changed and why.

<!-- ================================================================= END PROTECTED -->

---

## Goal

A self-hosted **MLTracker** — a web platform to track ML experiments across multiple projects and runs. Users log scalar metrics and images from training scripts via a REST API; the dashboard visualises them in real time.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11+, Flask (dev) / Gunicorn (prod) |
| Database | SQLite (`sqlite3` stdlib) |
| File storage | Local filesystem |
| Auth (browser) | Google OAuth 2.0 (`authlib`) |
| Auth (scripts) | Per-user API key (Bearer token) |
| Rate limiting | `flask-limiter` + Redis backend |
| Deployment | AWS EC2, Gunicorn + Nginx |
| UI | Vue 3 (Composition API, ESM via CDN import map) |
| Charts | Chart.js 4 (ESM via CDN) |
| Icons | Font Awesome 6 Solid (CDN) — everywhere, no Unicode glyphs |

### Theme

Dark by default; `body.light` class toggles light mode. All colours via CSS custom properties. Button hover is always `#2563eb` (blue-600) — never `--hover-bg`.

| Token | Dark | Light | Purpose |
|-------|------|-------|---------|
| `--bg` | `#1a1a2e` | `#f0f2f5` | Page background |
| `--panel` | `#16213e` | `#ffffff` | Sidebar / bar bg |
| `--panel2` | `#0f3460` | `#e8ecf4` | Secondary surface |
| `--border` | `#2a3a5e` | `#d0d8e8` | Dividers |
| `--accent` | `#e94560` | `#c0392b` | Highlight / active |
| `--text` | `#d0d8f0` | `#222233` | Primary text |
| `--text-dim` | `#7080a0` | `#667799` | Labels / icons |
| `--hover-bg` | `#1e2d50` | `#e8ecf4` | Tree row hover only |
| `--sel-bg` | `#0f3460` | `#ccd8f0` | Selected row bg |

---

## Project Structure

```
mltracker/
├── backend/
│   ├── app.py              Flask app factory + Gunicorn entry
│   ├── auth.py             Google OAuth + API key middleware
│   ├── config.py           Config object (reads all values from env vars)
│   ├── db.py               SQLite connection helpers + schema init
│   ├── storage.py          File save / URL helpers (save_image, delete_run_files)
│   ├── models.py           Pure dataclass helpers — no ORM
│   ├── routes/
│   │   ├── api.py          Write API — log scalars & images (API key auth)
│   │   ├── projects.py     Project CRUD (session auth)
│   │   ├── runs.py         Run CRUD + data retrieval (session auth)
│   │   └── admin.py        Admin dashboard API (first-user-is-admin, session auth)
│   └── requirements.txt
├── frontend/
│   ├── index.html          HTML shell + import maps (Vue, Chart.js, FA CDN)
│   ├── style.css           Layout and theming
│   └── app.js              Vue 3 app — all components
├── data/
│   ├── mltracker.db            SQLite database
│   └── mltracker/            Run data
│       └── <project_name>/<run_name>/
│           ├── metrics.jsonl
│           └── images/<step>_<key>.png
├── gunicorn.conf.py
├── nginx.conf
└── .gitignore              Excludes data/, .env, venv/, __pycache__/
```

---

## Layout

```
┌─────────────────────────────────────────────────┐  48px  TopBar
├──────────┬─┬───────────────────────────────────┤
│  Left    │▌│         Main Panel                 │  1fr
│  Panel   │▌│   (card grid — charts + images)    │
│  240px   │▌│                                    │
├──────────┴─┴───────────────────────────────────┤  26px  StatusBar
```

CSS Grid: `grid-template-columns: var(--left-w) 5px 1fr`. Handle `.resize-handle.lhandle` updates `--left-w` live (min 160px, max 480px).

---

## Authentication

### Browser — Google OAuth 2.0
- `/auth/login` → Google consent → `/auth/callback` → session cookie (`user_id`, `email`, `name`, `picture`)
- `/auth/logout` → clear session

### Scripts — API Key (Bearer token)
- 32-byte hex key via `secrets.token_hex(32)`, stored plain in `users.api_key`
- `Authorization: Bearer <key>` on every `/api/*` request
- `api_key_required` decorator sets `g.user_id`

> ⚠️ **Never accept API key as a query param — query strings end up in server logs.**

> ⚠️ **`/auth/me` must NOT use `@login_required`** — it's called by `fetch()` on startup.
> An unauthenticated request must return `{"logged_in": false}` as JSON, not a 302 redirect to HTML.

---

## Storage Architecture

### SQLite — metadata only

SQLite is **never written during `log()`** — only on run create, finish, resume.
Every connection: `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`.

```
users       id, google_id (unique), email, name, picture, api_key (unique), created_at
projects    id, user_id → users ON DELETE CASCADE, name, created_at; UNIQUE(user_id, name)
runs        id, project_id → projects ON DELETE CASCADE, name, status (running|finished|crashed),
            config (JSON blob), created_at, finished_at; UNIQUE(project_id, name)
```

### JSONL — scalar metrics + image refs

Each run: `data/mltracker/<project_name>/<run_name>/metrics.jsonl` (names sanitised via `_safe_name()`).

One JSON line per committed `log()` call:
```json
{"step": 0, "ts": 1718000000.1, "loss": 0.50, "acc": 0.70}
{"step": 1, "ts": 1718000005.3, "loss": 0.40, "pred": {"type": "image", "name": "1_pred.png"}}
```

`open(file, 'a') + write()` uses OS buffering — no fsync per step (~200× faster than SQLite WAL on Windows).

Images saved to `images/<step>_<key>.png`; only the filename stored inline in JSONL.
Cleanup: `shutil.rmtree(run_dir)` removes `metrics.jsonl` and `images/` together.

> ⚠️ **`metrics` and `images` SQLite tables do NOT exist.** All data is in JSONL files.

---

## REST API

All API routes under `/api/v1/`. Scripts use `Authorization: Bearer <api_key>`.

### Write endpoints (API key auth)

| Method | Path | Body | Notes |
|--------|------|------|-------|
| `POST` | `/api/v1/runs` | `{project, name, config?}` | Create run — idempotent by name |
| `POST` | `/api/v1/runs/<id>/log` | `{step, key: value, images: {key: b64}}` | Append one line to JSONL; **409 if not `running`** |
| `POST` | `/api/v1/runs/<id>/finish` | `{status}` | Mark `finished` or `crashed`; idempotent |
| `POST` | `/api/v1/runs/<id>/resume` | — | Re-open terminated run; **409 if already `running`** |

**Log payload:**
```json
{"step": 42, "loss": 0.312, "images": {"pred": "<base64-png>"}}
```
**Batch format** (SDK bundles multiple steps):
```json
{"steps": [{"step": 0, "ts": ..., "loss": 0.5}, {"step": 1, ...}]}
```

> ⚠️ **Every write endpoint (`/log`, `/finish`, `/resume`) MUST verify run ownership via JOIN:**
> `SELECT r.id FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=? AND p.user_id=?`
> Return 404 (not 403) on mismatch — don't reveal whether the run exists.

> ⚠️ **Status gate on `/log`:** Return 409 if status ≠ `running`.
> Body: `{"error": "Run is terminated. Call /resume to continue logging."}`

### Query endpoints (session auth)

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/projects` | List user's projects |
| `GET` | `/api/v1/projects/<id>/runs` | List runs in project |
| `GET` | `/api/v1/runs/<id>` | Run metadata + config |
| `GET` | `/api/v1/runs/<id>/metric-keys` | Distinct metric key names from JSONL |
| `GET` | `/api/v1/runs/<id>/metrics?keys=loss,acc&max_points=1000` | Time series; downsample in Python if len > max_points (default 1000, max 5000) |
| `GET` | `/api/v1/runs/<id>/image-keys` | Distinct image key names from JSONL |
| `GET` | `/api/v1/runs/<id>/images?key=pred` | `[{step, url}]` ordered by step, capped at 500 |
| `GET` | `/files/<path>` | Serve image file (`@login_required`, `send_from_directory`) |

### Delete endpoints (session auth)

| Method | Path | Notes |
|--------|------|-------|
| `DELETE` | `/api/v1/projects/<id>` | DB cascade + `shutil.rmtree(proj_dir)` |
| `DELETE` | `/api/v1/runs/<id>` | DB cascade + `shutil.rmtree(run_dir)` |

> ⚠️ **Check `cursor.rowcount` after DELETE.** If 0, return 404 — never silent 200.

> ⚠️ **Image validation order:** size check (`len(b64) > 27_000_000`) → `base64.b64decode(validate=True)` → `Image.MAX_IMAGE_PIXELS = 50_000_000` → `PIL.Image.open()` → convert RGB → save.
> Max 20 images per step. `images` field must be `{str: str}`.

> ⚠️ **Atomic image rollback:** track saved filenames; on any failure delete them before re-raising.

> ⚠️ **`INSERT OR IGNORE` + SELECT for run/project creation** — never SELECT-then-INSERT (TOCTOU race with 4 workers).

> ⚠️ **Input limits:** names ≤ 200 chars, step 0–10,000,000, config ≤ 64 KB. Reject with 400.

> ⚠️ **`get_json()` returns `None` on bad JSON** — always `if not isinstance(data, dict): return err(400)`.

> ⚠️ **SPA catch-all must guard API prefixes:**
> ```python
> if path.startswith(('api/', 'auth/', 'files/', 'health')): abort(404)
> ```
> Otherwise a typo'd `/api/v1/foo` returns HTML 200, silently breaking API clients.

### Admin endpoint (session auth)

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/admin/users` | All users + stats; 403 if not admin |

---

## Frontend

### Component Tree

```
App
├── TopBar        (logo · user avatar · API key copy · admin toggle · theme toggle · logout)
├── AdminPanel    (admin only — user stats table; replaces left+main when active)
├── LeftPanel     (project/run collapsible tree with status dots + trash icons)
├── MainPanel     (card grid with persistent layout)
│   ├── DashCard      (draggable + resizable wrapper)
│   │   ├── MetricChart   (Chart.js line chart)
│   │   └── ImageSlider   (step-nav + run image grid)
└── StatusBar     (project/run info + status badge)
```

### `dash` Data Shape

```js
{
  metrics:     { [key]: [{ label: run.name, points: [{step, value}] }] },
  image_cards: [{ key, label, runs: [{ label: run.name, images: [{step, url}] }] }],
  downsampled: bool
}
```

**Project view:** `image_cards` has one entry per image key; `runs` contains all project runs (empty `images: []` if that run has no data for this key).
**Run view:** same shape; `runs` is a single-element array.

### Dashboard Loaders

**`load_project_dash(proj)`** — fetch metric-keys and image-keys for all runs in parallel, then fetch all metric series and per-run images in parallel. One card per metric key (all runs overlaid); one card per image key (all runs side-by-side).

**`load_run_dash(run)`** — same shape, single run.

### Card Layout (MainPanel)

- `card_order` (ref[]) + `card_sizes` (ref{key: {w,h}}) drive rendering
- Layout saved to `localStorage` as `{order, sizes}` under key `wandb_layout_run_<id>` or `wandb_layout_proj_<id>`
- Restored on first data arrival for a selection

> ⚠️ **Watcher must use string key, not array:**
> ```js
> // WRONG — new array every render, always triggers
> watch(() => [proj?.id, run?.id], reset)
> // CORRECT
> watch(() => `${proj?.id ?? ''}_${run?.id ?? ''}`, reset)
> ```

> ⚠️ **Guard `save_layout` against empty transitional state:**
> ```js
> if (!card_order.value.length) return
> ```
> Without this, switching selection resets `card_order = []` then immediately saves empty layout.

**Drag reorder:** mousedown on drag bar → global mouseup reorders `card_order`.
**Resize:** mousedown on resize handle → track delta → update `card_sizes[key]` (min w:280, min h:150).
**Selection persistence:** last selected project/run saved to `localStorage` as `wandb_last_sel: {proj_id, run_id}` and restored on page load.

### MetricChart (Chart.js)

- Destroy before re-creating on data change
- Read CSS vars for colours at create time (theme-aware)
- `animation: false` for live runs
- `ResizeObserver` → `chart.resize()` on card resize
- Golden-angle colours: `hsl((idx * 137) % 360, 65%, 55%)`
- `backgroundColor: run_color(idx)` (same as `borderColor`) — fills legend dot
- Legend: `usePointStyle: true, pointStyle: 'circle', boxWidth: 6, boxHeight: 6`

### ImageSlider

Props: `runs: [{label, images: [{step, url}]}]`
Merged step list: `[...new Set(runs.flatMap(r => r.images.map(x => x.step)))].sort((a,b)=>a-b)`

- **Multi-run (`.img-grid-multi`):** CSS grid `repeat(auto-fill, minmax(140px, 1fr))`; each cell is a framed card (panel2 bg + border) with run name header on top
- **Single-run (`.img-grid-single`):** image fills full card body; no frame

### Auto-refresh

`setTimeout`-rescheduled (not `setInterval`). 5s base; exponential backoff on failures up to 60s; reset to 5s on success. **Always cleared** on selection change or unmount.

**Image auto-advance:** `ImageSlider` watches total image count; when it increases (new step logged), `_idx` jumps to the last step automatically.

### Admin Dashboard

- **First user** (lowest `id` in `users` table) is admin — determined at runtime, no schema change.
- `/auth/me` includes `is_admin: bool` — checked on every load via `SELECT MIN(id) FROM users`.
- `GET /api/v1/admin/users` — returns all users with: `project_count`, `run_count`, `total_run_seconds` (sum of `finished_at - created_at` for finished runs), `last_active`, `created_at`. Guarded by `admin_required` decorator (403 for non-admins).
- **TopBar** shows `fa-users-gear` button only when `user.is_admin`; highlighted (accent colour) when active.
- **AdminPanel** replaces LeftPanel + MainPanel when toggled; shows a table with avatar, name, email, stats. First-user row has accent tint + ★.

> ⚠️ **Admin check must query `MIN(id)` at request time** — never store the admin flag in the session cookie (it persists across user deletions and wouldn't update if the first user changes).

> ⚠️ **Interval leak:** failing to clear on selection change causes multiple overlapping refresh timers that escalate API request rate as the user clicks around.

---

## Client SDK (`mltracker.py`)

Single file, only `requests` dependency.

```python
run = wandb.init(project="mnist", name="exp", config={"lr": 0.001})
# run.name = "exp_a3f2b1"  (6-char hex suffix appended)

run.log({"loss": 0.3, "acc": 0.9}, step=0)          # async, returns immediately
run.log({"loss": 0.3}, commit=False)                  # buffer
run.log({"img": wandb.Image(arr)})                    # commit=True — flushes buffer
run.finish()                                          # waits for all POSTs, marks done

run = wandb.resume(project="mnist", name="exp_a3f2b1")
```

**`wandb.Image(data, bgr=False, caption='')`** — wraps numpy array or PIL Image.
`bgr=True` flips channels BGR→RGB (`raw[..., ::-1]`) before encoding.

**Async posting:** `log()` enqueues to a `queue.Queue`; background daemon thread drains it.
**Batching:** thread greedily drains queue → single POST with `{"steps": [...]}` when multiple steps queued.
**`finish()`:** flushes buffer, sends `None` sentinel, joins thread, POSTs `/finish`.

**Crash/interrupt handling:**
- Module-level `sys.excepthook` sets `run._crash_status = 'crashed'` on all active runs
- `atexit.register(run._auto_finish)` calls `finish(status='crashed')` if `finish()` was never called
- `_active_runs` set tracks all live runs; removed on `finish()`

**Credentials:** `WANDB_API_KEY` (required) and `WANDB_HOST` (default `http://localhost:5000`) from env vars, or passed explicitly.

---

## Security Checklist

<!-- =================================================================
     PROTECTED — DO NOT EDIT THIS SECTION WITHOUT EXPLICIT USER APPROVAL
     These security rules are non-negotiable and must not be silently dropped.
     ================================================================= -->

- **Session cookie:** `HTTPONLY=True`, `SAMESITE='Lax'`, `SECURE` from env (True in prod).
- **API key:** `secrets.token_hex(32)`; stored plain; only in `Authorization` header, never query param.
- **Run ownership:** every write AND read endpoint verifies `p.user_id = g.user_id` via JOIN. Valid key cannot read/modify another user's data.
- **Status gate:** `/log` returns 409 if status ≠ `running`. Only `/resume` re-opens.
- **DELETE 404:** check `cursor.rowcount`; return 404 if 0 — never silent 200.
- **File serving:** `/files/<path>` requires `@login_required`. Use `send_from_directory` (not manual joins) — prevents path traversal.
- **Image validation:** size check BEFORE decode; `base64.b64decode(validate=True)`; `Image.MAX_IMAGE_PIXELS = 50_000_000` before `Image.open()`; max 20 images/step; `images` must be `{str: str}`.
- **Image atomicity:** all files saved before JSONL append; on failure delete saved files (no orphans).
- **File cleanup:** `shutil.rmtree` after DB DELETE to prevent EBS fill.
- **Input limits:** names ≤ 200 chars, step 0–10M, config ≤ 64 KB.
- **Path sanitisation:** `_safe_name()` on all user-supplied path components. Never use raw project/run/key names as filesystem paths.
- **`get_json()` guard:** always `isinstance(data, dict)` before field access.
- **SPA catch-all guard:** abort(404) for `api/`, `auth/`, `files/`, `health` prefixes.
- **Rate limiting:** `flask-limiter` with **Redis backend** (not in-memory — in-memory is per-worker, effective limit = 4× configured with 4 workers). Apply to `/log`, `POST /runs`, `/auth/regenerate-key`.
- **Cascade deletes:** `ON DELETE CASCADE` on all FK relationships.
- **Secrets:** in `/etc/mltracker.env` (mode 600, root-owned), loaded via systemd `EnvironmentFile=`. Never in `/etc/environment` (world-readable).
- **OAuth state:** `authlib` validates state automatically — do not bypass.
- **CDN SRI:** every CDN resource has pinned exact version + `integrity="sha384-..."` + `crossorigin="anonymous"`. Note: SRI in import maps requires Chrome 112+ / Firefox 126+.
- **API key storage:** stored plain (known tradeoff for personal tool). If DB leaks, all keys exposed — mitigated by mode-600 env file and dedicated EBS volume.
- **SQL:** always parameterised. Dynamic `IN` clauses built by placeholder count, not string format.

<!-- ================================================================= END PROTECTED -->

---

## Deployment (AWS EC2)

**Instance:** `t3.small`, Ubuntu 22.04, 20 GB root + dedicated EBS for `data/`.
**Inbound:** ports 22 (your IP), 80, 443.

**Key config:**
- Gunicorn: `bind=127.0.0.1:8000`, `workers=4`, `worker_class=sync`, `timeout=120`
- Nginx: serves `frontend/` static; proxies `/(api|auth|files|health)/` to `:8000`; `client_max_body_size 20m`
- HTTPS via Let's Encrypt (`certbot --nginx`); set `SESSION_COOKIE_SECURE=true` after
- Systemd service with `EnvironmentFile=/etc/mltracker.env`, `Restart=on-failure`, `PrivateTmp=true`

**Secrets file `/etc/mltracker.env` (mode 600):**
```
SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
DB_PATH=/mnt/wandb_data/mltracker.db
FILES_DIR=/mnt/wandb_data/files
SESSION_COOKIE_SECURE=true
```

**Maintenance:**
- Update: `git pull` → `pip install -r requirements.txt` → `systemctl restart mltracker`
- Logs: `journalctl -u mltracker -f`, `/var/log/gunicorn/`, `/var/log/nginx/`
- DB backup: `sqlite3 mltracker.db ".backup '/mnt/backup/wandb_$(date +%Y%m%d).db'"` — backup target must be a **separate EBS volume or S3**, not the same data volume

> ⚠️ **Log rotation required** — without logrotate, Gunicorn logs fill the root volume. Rotate daily, keep 14 days compressed, use `copytruncate`.

---

## Implementation Checklist

```
[x] Phase 1: config.py + db.py + app.py          → GET /health 200
[x] Phase 2: auth.py + Google OAuth               → /auth/me returns user + api_key
[x] Phase 3: storage.py + all API routes          → full log/query/delete cycle works
[x] Phase 4: frontend (index.html, style.css, app.js) → dashboard shows charts + images
[x] Phase 5: mltracker.py SDK                   → end-to-end script → dashboard shows data
[ ] Deployment: EC2 + Gunicorn + Nginx + HTTPS
```

**Deployment verification:**
1. `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`
2. `curl https://yourdomain.com/health` → `{"status":"ok"}`
3. Browser login → Google → dashboard
4. Remote SDK script logs 20 steps → charts appear
5. Two concurrent SDK scripts → both runs appear, no errors
6. Cross-user log attempt → 404
