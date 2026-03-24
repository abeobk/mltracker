# PROGRAM.md

## Agent Workflow Rules

- Read SKILL.md before starting any task.
- Always plan before writing code; log steps in `log.md` (append only, never delete).
- Develop knowhow continuously and write it to **`SKILL.md`**.
- **Before every `git commit`** — all three must be updated:
  1. `log.md` — append entry describing what changed and why
  2. `PROGRAM_update.md` — evolve the spec to reflect the new state; must always be clearer, more accurate, and more elaborated than `PROGRAM.md`
  3. `SKILL.md` — add any new skill, gotcha, or pattern acquired
- Auto-commit on each bug fix; write a detailed commit message.
- `PROGRAM_update.md` is the single authoritative spec — supersedes `PROGRAM.md`

---

## Goal

A self-hosted **WandB clone** — a web platform to track ML experiments across multiple projects and runs. Users log scalar metrics and images from training scripts via a REST API; the dashboard visualises them in real time.

---

## Technology Stack

| Layer           | Technology |
|-----------------|------------|
| Backend server  | Python 3.11+, Flask (dev) / Gunicorn (prod) |
| Database        | SQLite (via Python `sqlite3` stdlib) |
| File storage    | Local filesystem; paths stored in DB |
| Authentication  | Google OAuth 2.0 (`authlib`) — browser login |
| API auth        | Per-user API key (Bearer token) — script upload |
| Rate limiting   | `flask-limiter` — in-memory per-key and per-session limits |
| Deployment      | AWS EC2, Gunicorn + Nginx reverse proxy |
| UI framework    | Vue 3 (Composition API, ESM via CDN unpkg import map) |
| Charts          | Chart.js 4 (ESM via CDN) |
| Icons           | Font Awesome 6 Solid (CDN) — **everywhere**, no Unicode glyphs |

### Theme
- **Dark** by default; user toggles to light via toolbar `fa-sun`/`fa-moon` button.
- Implemented with CSS custom properties only — toggling adds/removes `body.light` class.
- **Button hover**: always `#2563eb` (blue-600) with white text. `--hover-bg` for tree rows only.

| Token        | Dark       | Light      | Purpose |
|--------------|------------|------------|---------|
| `--bg`       | `#1a1a2e`  | `#f0f2f5`  | Page background |
| `--panel`    | `#16213e`  | `#ffffff`  | Sidebar / bar bg |
| `--panel2`   | `#0f3460`  | `#e8ecf4`  | Secondary surface |
| `--border`   | `#2a3a5e`  | `#d0d8e8`  | Dividers |
| `--accent`   | `#e94560`  | `#c0392b`  | Highlight / active |
| `--text`     | `#d0d8f0`  | `#222233`  | Primary text |
| `--text-dim` | `#7080a0`  | `#667799`  | Labels / icons |
| `--hover-bg` | `#1e2d50`  | `#e8ecf4`  | Row hover (tree items) |
| `--sel-bg`   | `#0f3460`  | `#ccd8f0`  | Selected row bg |

---

## Project Structure

```
wandb_clone/
├── backend/
│   ├── app.py              Flask app factory + Gunicorn entry
│   ├── auth.py             Google OAuth + API key middleware
│   ├── config.py           Config object (reads all values from env vars)
│   ├── db.py               SQLite connection helpers + schema init
│   ├── storage.py          File save / URL-path helpers (save_image, delete_run_files)
│   ├── models.py           Pure dataclass helpers — no ORM (RunRecord, MetricRow, etc.)
│   ├── routes/
│   │   ├── api.py          REST API — log scalars & images (API key auth)
│   │   ├── projects.py     CRUD for projects (session auth)
│   │   └── runs.py         CRUD for runs + data retrieval (session auth)
│   └── requirements.txt
├── frontend/
│   ├── index.html          HTML shell + import maps (Vue, Chart.js, FA CDN)
│   ├── style.css           Layout and theming (CSS grid + custom props)
│   └── app.js              Vue 3 app — all components
├── data/
│   ├── wandb.db            SQLite database (auto-created on first run)
│   └── files/              Uploaded images / artifacts
│       └── <project_id>/<run_id>/<step>_<key>.png
├── gunicorn.conf.py        Production worker config
├── nginx.conf              Reverse-proxy config (reference)
└── .gitignore              Excludes data/, .env, venv/, __pycache__/
```

---

## Layout

```
┌──────────────────────────────────────────────────────────┐  48px
│  Top Bar  (logo · project name · user avatar · logout)   │
├──────────┬─┬────────────────────────────────────────────┤
│          │▌│                                            │
│  Left    │▌│           Center Dashboard                 │  1fr
│  Panel   │▌│   (metric charts + image sliders)          │
│  240px   │▌│                                            │
│(project/ │▌│                                            │
│  run     │▌│                                            │
│  tree)   │▌│                                            │
├──────────┴─┴────────────────────────────────────────────┤  26px
│  Status Bar  (selected project / run brief info)         │
└──────────────────────────────────────────────────────────┘
             ▌ = 5px drag handle (col-resize cursor)
```

### CSS Grid (3-column)
- `grid-template-columns: var(--left-w) 5px 1fr`
- Areas: `topbar | left + lhandle + main | status`
- `--left-w` default `240px`; updated live during drag; min `160px`, max `480px`
- Handle class: `.resize-handle.lhandle`

---

## Authentication

### Browser Login — Google OAuth 2.0
1. User visits `/` → redirected to `/auth/login` if no valid session.
2. `/auth/login` → redirect to Google consent screen.
3. Google calls `/auth/callback` with `code` → server exchanges for tokens → stores `user_id`, `email`, `name`, `picture` in session cookie.
4. `/auth/logout` → clears session, redirects to login page.

### Script Upload — API Key (Bearer token)
- Each user has one API key stored in DB (`users.api_key`, random 32-byte hex via `secrets.token_hex(32)`).
- Clients send `Authorization: Bearer <api_key>` on every request to `/api/*`.
- Middleware in `auth.py` validates key, sets `g.user_id`.
- Key visible on user settings page; one-click regenerate.

---

## Database Schema (SQLite)

All foreign keys use `ON DELETE CASCADE`. `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL` are set on every connection.

```
users
  id           INTEGER PRIMARY KEY AUTOINCREMENT
  google_id    TEXT UNIQUE NOT NULL
  email        TEXT NOT NULL
  name         TEXT
  picture      TEXT
  api_key      TEXT UNIQUE NOT NULL
  created_at   REAL NOT NULL DEFAULT (unixepoch('now'))

projects
  id           INTEGER PRIMARY KEY AUTOINCREMENT
  user_id      INTEGER NOT NULL → users(id) ON DELETE CASCADE
  name         TEXT NOT NULL
  created_at   REAL NOT NULL DEFAULT (unixepoch('now'))
  UNIQUE(user_id, name)

runs
  id           INTEGER PRIMARY KEY AUTOINCREMENT
  project_id   INTEGER NOT NULL → projects(id) ON DELETE CASCADE
  name         TEXT NOT NULL
  status       TEXT NOT NULL DEFAULT 'running'   -- running | finished | crashed
  config       TEXT                              -- JSON blob
  created_at   REAL NOT NULL DEFAULT (unixepoch('now'))
  finished_at  REAL
  UNIQUE(project_id, name)

metrics
  id      INTEGER PRIMARY KEY AUTOINCREMENT
  run_id  INTEGER NOT NULL → runs(id) ON DELETE CASCADE
  step    INTEGER NOT NULL
  key     TEXT NOT NULL
  value   REAL NOT NULL
  ts      REAL NOT NULL DEFAULT (unixepoch('now'))
  UNIQUE(run_id, step, key)          ← prevents duplicate log entries
  INDEX ON (run_id, key)

images
  id      INTEGER PRIMARY KEY AUTOINCREMENT
  run_id  INTEGER NOT NULL → runs(id) ON DELETE CASCADE
  step    INTEGER NOT NULL
  key     TEXT NOT NULL
  path    TEXT NOT NULL              -- relative to data/files/
  ts      REAL NOT NULL DEFAULT (unixepoch('now'))
  UNIQUE(run_id, step, key)          ← re-logging same step replaces the image row
  INDEX ON (run_id, key)
```

---

## REST API

All API routes are under `/api/v1/`. Script clients use `Authorization: Bearer <api_key>`.

### Write endpoints (script → server, API key auth)

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/runs` | `{project, name, config?}` | Create a new run (idempotent — returns existing run if name matches) |
| `POST` | `/api/v1/runs/<run_id>/log` | `{step, scalar_key: value, images: {key: base64}}` | Log one step; **rejected with 409 if run is not `running`** |
| `POST` | `/api/v1/runs/<run_id>/finish` | `{status}` | Mark run `finished` or `crashed`; only owner can call this |
| `POST` | `/api/v1/runs/<run_id>/resume` | — | Reopen a terminated run for logging; only owner can call this |

**Run ownership rule:** Every write endpoint (`/log`, `/finish`, `/resume`) verifies that the run's project belongs to the authenticated user (`g.user_id`). A valid API key holder cannot modify another user's run.

**Status gate on `/log`:** If the run status is `finished` or `crashed`, `/log` returns `409 Conflict` with the message `"Run is terminated. Call /resume to continue logging."` The run must be explicitly resumed before new data is accepted.

**Log payload format:**
```
{
  "step": 42,
  "loss": 0.312,
  "acc":  0.891,
  "images": {
    "input": "<base64-png>",
    "pred":  "<base64-png>"
  }
}
```
- Number values → `metrics` table (keyed by the field name)
- `images` dict → each PNG decoded, validated, saved to `data/files/<proj_id>/<run_id>/<step>_<key>.png`; path written to `images` table

### Query endpoints (dashboard → server, session auth)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/projects` | List user's projects |
| `GET` | `/api/v1/projects/<id>/runs` | List runs in a project |
| `GET` | `/api/v1/runs/<id>` | Run metadata + config |
| `GET` | `/api/v1/runs/<id>/metric-keys` | List distinct metric key names (ownership required) |
| `GET` | `/api/v1/runs/<id>/metrics?keys=loss,acc&max_points=1000` | Time series for requested keys, grouped by key; `max_points` (default 1000, max 5000) triggers server-side even-interval downsampling — prevents multi-million-row responses on long runs |
| `GET` | `/api/v1/runs/<id>/image-keys` | List distinct image key names (ownership required) |
| `GET` | `/api/v1/runs/<id>/images?key=input` | List of `{step, url}` for an image key; `key` param required; capped at 500 entries (last 500 steps if more exist) |
| `GET` | `/files/<path>` | Serve a stored image file (login required) |

### Delete endpoints (dashboard → server, session auth)

| Method | Path | Description |
|--------|------|-------------|
| `DELETE` | `/api/v1/projects/<id>` | Delete project + all DB rows + image files from disk; returns 404 if not found or not owned |
| `DELETE` | `/api/v1/runs/<id>` | Delete run + all DB rows + image files from disk; returns 404 if not found or not owned |

---

## Frontend — UI Panels

### Top Bar (left → right)

| Element | Icon / text | Notes |
|---------|-------------|-------|
| Logo | `fa-chart-line` + "WandB Clone" | Links to `/` |
| Spacer | — | `flex:1` |
| User avatar | `<img>` from Google picture | 28px circle |
| Username | `user.name` | text |
| API Key | `fa-key` | copies key to clipboard; settings page regenerate button shows a confirmation warning: "This will immediately invalidate all running scripts using the current key." |
| Theme | `fa-sun` / `fa-moon` | toggles `body.light` |
| Logout | `fa-right-from-bracket` | `GET /auth/logout` |

### Left Panel — Project / Run Tree

Two-level collapsible tree.

**Project row:**
- `fa-chevron-right` / `fa-chevron-down` collapse toggle
- `fa-folder` icon
- Project name (truncated, tooltip on overflow)
- `fa-trash` delete — hover-only; confirms before deleting

**Run row (child):**
- 10px status dot: green=running, grey=finished, red=crashed
- Run name (truncated, tooltip)
- `fa-trash` delete — hover-only

Selected row: 2px `--accent` left border + `--sel-bg` background.

### Center Dashboard

| Selection | Dashboard shows |
|-----------|----------------|
| Project   | All runs overlaid on each metric chart (one chart per key). Legend = run names. |
| Run       | Only that run's data. One chart per scalar key + one image slider per image key. |
| None      | Empty state: `fa-chart-line` + "Select a project or run" |

**Metric chart (Chart.js Line):**
- X-axis: step; Y-axis: metric value
- Each run = one dataset with a distinct colour (golden-angle hue: `hsl((idx * 137) % 360, 65%, 55%)`)
- Theme-aware: read CSS custom properties for grid/label colours at chart creation time
- `animation: false` when auto-refreshing a live run

**Image slider (per image key):**
- Shows one image at a time
- Step control: `fa-backward-step` / `fa-forward-step` + step label
- Image fills card width, max 400px height

**Auto-refresh:** When a `running` run is selected, refresh data every 5 seconds.
The interval timer MUST be cleared when the selection changes or the component unmounts.

### Status Bar (left → right)
- `fa-folder` + project name (if project or run selected)
- `fa-person-running` + run name + status badge (if run selected)
- Right-aligned: run count (project view) or total step count (run view)

---

## Naming Conventions

| Kind | Convention | Example |
|------|------------|---------|
| Python classes | PascalCase | `RunRecord`, `MetricRow` |
| Python funcs/methods | snake_case | `get_run`, `log_metrics` |
| Python variables | snake_case | `run_id`, `api_key` |
| Python constants | SCREAMING_SNAKE | `DB_PATH`, `FILES_DIR` |
| JS classes | PascalCase | `ChartPanel` |
| JS member vars | `_snake_case` | `_charts`, `_sel_run` |
| JS methods | camelCase | `loadMetrics`, `renderCharts` |
| JS locals | snake_case | `run_id`, `chart_data` |
| Booleans | `is_` / `has_` / `can_` prefix | `is_loading`, `has_images` |

---

## Client SDK (`wandb_clone.py`)

Single-file SDK, no dependencies beyond `requests`.

Usage:
```
run = wandb.init(project="mnist", name="exp-001", api_key="<key>", host="http://localhost:5000")
run.log({"loss": 0.3, "acc": 0.9}, step=0)
run.finish()

# To continue a finished/crashed run:
run.resume()
run.log({"loss": 0.2}, step=100)
run.finish()
```

Methods:
- `init(project, name, api_key, host, config)` → `Run` object
- `Run.log(data, step)` → POST `/api/v1/runs/<id>/log`; images encoded as base64 PNG if value is a PIL Image or numpy array
- `Run.finish(status='finished')` → POST `/api/v1/runs/<id>/finish`
- `Run.resume()` → POST `/api/v1/runs/<id>/resume`; raises a clear error if the server rejects it

---

## Deployment

### Local dev
```
cd backend && flask --app app run --port 5000 --debug
```

### Production (AWS EC2)
```
gunicorn -c gunicorn.conf.py "app:create_app()"
```
- Workers: 4 sync workers (2× CPU cores)
- Bind to `127.0.0.1:8000`; Nginx proxies 80/443 → 8000
- HTTPS via Let's Encrypt
- `data/` on a dedicated persistent EBS volume

Secrets are stored in `/etc/wandb_clone.env` (mode 600, root-owned), loaded via systemd `EnvironmentFile=`.

---

## Security Checklist

- **Session cookie:** `HTTPONLY=True`, `SAMESITE='Lax'`, `SECURE` read from env (True in prod).
- **API key:** generated with `secrets.token_hex(32)`; stored plain; sent only in `Authorization` header (never query param).
- **Run ownership:** every write endpoint (`/log`, `/finish`, `/resume`) AND every read endpoint (`/metrics`, `/metric-keys`, `/images`, `/image-keys`) verifies the run's project belongs to `g.user_id` or `session['user']['id']` via a JOIN. A valid key cannot read or modify another user's data.
- **Status gate:** `/log` returns 409 if the run is `finished` or `crashed`. Only an explicit `/resume` call re-opens it.
- **DELETE 404 on mismatch:** `DELETE /projects/<id>` and `DELETE /runs/<id>` check `cursor.rowcount` after the query. If 0 rows deleted (resource doesn't exist or belongs to another user), return 404 — never silent 200.
- **File serving:** `/files/<path>` requires `@login_required`. Uses `send_from_directory` (not manual joins) to prevent path traversal.
- **Image validation:** size check BEFORE `base64.b64decode`; decode with `base64.b64decode(data, validate=True)` (rejects malformed base64 cleanly before reaching PIL); PIL validates image content; `Image.MAX_IMAGE_PIXELS` set to 50,000,000 before any `Image.open()` to prevent decompression bomb attacks (a small compressed file that expands to gigabytes in RAM); path built from integer IDs + character-sanitised key only (alphanumeric, `-`, `_` only); max 20 images per log step; `images` field must be a `{str: str}` dict.
- **Image atomicity:** all image files are saved before the DB transaction; if any save or the transaction fails, previously saved files are deleted to prevent disk orphans.
- **File cleanup on delete:** deleting a project or run removes the image directory from disk (`shutil.rmtree`) after the DB DELETE succeeds, preventing EBS volume fill-up.
- **Input size limits:** project/run names ≤ 200 chars; metric/image key names non-empty, ≤ 200 chars; config blob ≤ 64 KB; step ≥ 0 and ≤ 10,000,000; all rejected with 400 if exceeded.
- **Request body validation:** `get_json()` result must be a `dict`, not `None` — validate before any field access to avoid AttributeError 500s.
- **`/auth/me` returns JSON always:** never uses `@login_required`; returns `{"logged_in": false}` when unauthenticated so the frontend `fetch()` can parse it correctly without chasing a redirect to an HTML page.
- **SQL:** always parameterised queries. Dynamic `IN` clauses built via placeholder count, not string formatting.
- **CDN integrity:** every external CDN resource (Vue, Chart.js, Font Awesome) uses a pinned exact version and an `integrity="sha384-..."` SRI attribute with `crossorigin="anonymous"`. Never use floating version tags (`vue@3`).
- **Rate limiting:** `flask-limiter` applied to `/log`, `POST /runs`, and `/auth/regenerate-key` to prevent flooding and brute-force enumeration. The limiter **must** use a shared storage backend (Redis) — the default in-memory backend is per-worker, so with 4 Gunicorn workers the effective limit is 4× the configured value, rendering it useless for flood protection.
- **Cascade deletes:** schema uses `ON DELETE CASCADE` on all FK relationships so deletions do not leave orphaned rows.
- **Secrets:** `SECRET_KEY`, Google credentials, and paths in a dedicated mode-600 env file (`/etc/wandb_clone.env`) loaded via systemd `EnvironmentFile=`. Never hard-coded, never in `/etc/environment`.
- **OAuth state:** `authlib`'s built-in state validation prevents CSRF on the OAuth callback — do not implement a custom callback flow.
- **API key storage:** stored plain text (not hashed) — a deliberate tradeoff: SHA-256 hashing would add a lookup cost and this is a personal/team tool, not a public SaaS. Known risk: if the DB file leaks, all keys are exposed. Mitigated by mode-600 env file and DB on a dedicated EBS volume. Do not change to hashed without documenting the migration path.
- **SPA catch-all route:** the Flask catch-all that serves `index.html` for unknown paths MUST explicitly check `path.startswith(('api/', 'auth/', 'files/', 'health'))` and call `abort(404)` for those prefixes — otherwise a nonexistent `/api/v1/foo` route returns HTML 200 instead of JSON 404, silently breaking API clients.
- **Sensitive files:** `.gitignore` must exclude `data/`, `.env`, `backend/venv/`, `__pycache__/` to prevent accidental commit of the SQLite DB, secrets, or uploaded images.
