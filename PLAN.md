# PLAN.md — WandB Clone: Implementation, Test & Deployment Plan

---

## Part 1 — Implementation Plan

### Overview

Five sequential phases. Each phase must be fully working before moving to the next.
Never start UI work without the API being testable via `curl`.

```
Phase 1 → Backend scaffold + DB
Phase 2 → Auth (Google OAuth + API key)
Phase 3 → Core API (log + query)
Phase 4 → Frontend (Vue 3 SPA)
Phase 5 → Client SDK (wandb_clone.py)
```

---

### Phase 1 — Backend Scaffold + Database

**Goal:** Flask app starts, DB initialises on first run, health endpoint returns 200.

#### 1.1 Directory layout

```
wandb_clone/
├── backend/
│   ├── app.py              Flask app factory + Gunicorn entry
│   ├── config.py           Config object (reads env vars)
│   ├── db.py               SQLite connection helpers + schema init
│   ├── auth.py             Google OAuth + API key middleware
│   ├── storage.py          File save / URL-path helpers
│   ├── models.py           Pure data-class helpers (no ORM)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── api.py          REST API — log scalars & images (API key auth)
│   │   ├── projects.py     CRUD for projects (session auth)
│   │   └── runs.py         CRUD for runs + data retrieval (session auth)
│   └── requirements.txt
├── frontend/
│   ├── index.html          HTML shell + import maps (Vue, Chart.js, FA CDN)
│   ├── style.css           Layout and theming (CSS grid + custom props)
│   └── app.js              Vue 3 app — all components
├── data/
│   └── files/              Created by storage.py on first use
├── gunicorn.conf.py
├── nginx.conf
└── .gitignore              # data/, .env, backend/venv/, __pycache__/, *.pyc
```

> ⚠️ **IMPORTANT NOTE [Security — .gitignore required from day one]**
> Create `.gitignore` in Phase 1 before any `git init` or first commit.
> `data/wandb.db` is inside the repo tree — an accidental `git add .` permanently commits
> the database (including all user data and plain-text API keys) into git history.
> Minimum entries: `data/`, `.env`, `backend/venv/`, `__pycache__/`, `*.pyc`.

#### 1.2 Dependencies (`requirements.txt`)

| Package | Purpose |
|---------|---------|
| `flask>=3.0` | Web framework |
| `authlib>=1.3` | Google OAuth client |
| `requests>=2.31` | Used by client SDK |
| `Pillow>=10.0` | Decode + validate uploaded images |
| `gunicorn>=21.0` | Production WSGI server |
| `python-dotenv>=1.0` | Load `.env` in dev |
| `flask-limiter>=3.0` | Rate limiting on API and auth endpoints |
| `redis>=5.0` | Shared rate-limiter storage backend (required for multi-worker correctness) |

#### 1.3 Configuration (`config.py`)

Read all secrets and paths from environment variables at startup.
Never hard-code any value.

Config object exposes:
- `SECRET_KEY` — raise an error at startup if missing
- `DB_PATH` — SQLite file path (default `../data/wandb.db`)
- `FILES_DIR` — image storage root (default `../data/files`)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — OAuth credentials
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = 'Lax'`
- `SESSION_COOKIE_SECURE` — read from env as a boolean string (`'true'`/`'false'`), default `False` for dev

> ⚠️ **IMPORTANT NOTE [Security #5 — SESSION_COOKIE_SECURE]**
> `SESSION_COOKIE_SECURE` MUST be read from an environment variable, not hardcoded.
> The production deployment sets `SESSION_COOKIE_SECURE=true` in the environment file.
> If hardcoded to `False`, session cookies will be sent over plain HTTP even in production,
> exposing session tokens on the wire.

#### 1.4 Database schema (`db.py`)

The schema is executed once on startup via `CREATE TABLE IF NOT EXISTS`.
Every new connection must enable WAL mode and foreign key enforcement before use.

**Tables:**

`users` — one row per Google account:
- `id`, `google_id` (unique), `email`, `name`, `picture`, `api_key` (unique), `created_at`

`projects` — owned by a user:
- `id`, `user_id` (→ users), `name`, `created_at`
- Unique on `(user_id, name)`
- **Delete cascades to runs**

`runs` — belongs to a project:
- `id`, `project_id` (→ projects), `name`, `status` (`running` / `finished` / `crashed`), `config` (JSON blob), `created_at`, `finished_at`
- Unique on `(project_id, name)`
- **Delete cascades to metrics and images**

`metrics` — scalar data points:
- `id`, `run_id` (→ runs), `step`, `key`, `value`, `ts`
- Index on `(run_id, key)` for fast queries
- **Unique on `(run_id, step, key)`** — prevents duplicate log entries for the same step

`images` — uploaded image references:
- `id`, `run_id` (→ runs), `step`, `key`, `path` (relative to `data/files/`), `ts`
- Index on `(run_id, key)` for fast queries
- **Unique on `(run_id, step, key)`** — re-logging the same step replaces the row (same as metrics)

> ⚠️ **IMPORTANT NOTE [Correctness #2, #12 — Cascade Deletes]**
> Every foreign key relationship MUST be declared with `ON DELETE CASCADE`.
> Deleting a project must automatically delete all its runs, metrics, and images.
> Deleting a run must automatically delete all its metrics and images.
> Without CASCADE, deleting a project with runs will raise a FK constraint error
> (because `PRAGMA foreign_keys=ON` is required). Manual multi-table deletes are
> error-prone — use CASCADE in the schema instead.

> ⚠️ **IMPORTANT NOTE [Data Integrity #8 — Duplicate Metrics]**
> The `metrics` table MUST have `UNIQUE(run_id, step, key)`.
> Without this, re-logging the same step (e.g. on retry) silently appends a duplicate row.
> Charts will then show double values at that step.
> On conflict, use `INSERT OR REPLACE` to overwrite the old value.

#### 1.5 App factory (`app.py`)

The app is created by a factory function so Gunicorn can import it cleanly.

Steps inside the factory:
1. Create the Flask app with the `frontend/` folder as the static root
2. Load config from the Config object
3. Initialise the DB (run schema creation)
4. Register the teardown hook that closes the DB connection after each request
5. Register all blueprints with their URL prefixes:
   - `/api/v1` → api blueprint (API key auth)
   - `/auth` → auth blueprint (OAuth)
   - `/api/v1` → projects blueprint (session auth)
   - `/api/v1` → runs blueprint (session auth)
6. Register a catch-all SPA route that serves `index.html` for any path that is not an API, auth, files, or health path — the catch-all MUST explicitly guard against API prefixes (see pattern below) so that a nonexistent `/api/v1/foo` returns a JSON 404, not an HTML 200 that silently breaks API clients:
   ```python
   @app.route('/', defaults={'path': ''})
   @app.route('/<path:path>')
   def spa(path):
       if path.startswith(('api/', 'auth/', 'files/', 'health')):
           abort(404)
       return send_from_directory(app.static_folder, 'index.html')
   ```
7. Register the `/files/<path>` route to serve uploaded images

> ⚠️ **IMPORTANT NOTE [Security #3 — File Serving Auth]**
> The `/files/<path>` route MUST require login (`@login_required`).
> Without it, anyone who guesses a URL like `/files/1/1/0_loss.png` can
> download images from any user's run without authenticating.
> Use `send_from_directory` (not manual path joins) to prevent path traversal.

**Checkpoint:** `GET /health` returns `{"status": "ok"}` AND verifies the DB is reachable (runs `SELECT 1` — if the DB file is missing or unreadable, return `{"status": "error"}` with 500 so health checks catch DB failures, not just process startup).

---

### Phase 2 — Authentication

**Goal:** Google login flow works end-to-end; `/auth/me` returns user info + API key.

#### 2.1 Google OAuth setup (one-time, in Google Cloud Console)

1. Create an OAuth 2.0 Web Application credential.
2. Add authorised redirect URIs:
   - `http://localhost:5000/auth/callback` (dev)
   - `https://yourdomain.com/auth/callback` (prod)
3. Copy Client ID and Secret into `.env`. Never commit `.env`.

#### 2.2 `auth.py` — routes and middleware

**`init_oauth(app)`** — called inside the factory:
- Registers the Google OAuth client using the OIDC discovery URL
- Requests scopes: `openid email profile`

**`login_required` decorator** (for browser-session routes):
- Check if `user` key is present in the Flask session
- If missing, redirect to `/auth/login`

**`api_key_required` decorator** (for API routes):
- Read the `Authorization` header; it must start with `Bearer `
- Look up the key in the `users` table
- If found, set `g.user_id` for the rest of the request
- If missing or invalid, return 401 JSON error

> ⚠️ **IMPORTANT NOTE [Security — Rate Limiting]**
> The `api_key_required` decorator performs a DB lookup on every API call. Without rate
> limiting, a script can hammer this endpoint to enumerate valid keys or flood `/log` with
> junk data filling disk and DB. Apply rate limits using `flask-limiter`:
> - `POST /api/v1/runs/<id>/log` — 60 requests/minute per API key
> - `POST /api/v1/runs` — 30 requests/minute per API key
> - `POST /auth/regenerate-key` — 5 requests/minute per session
> Add `flask-limiter` to `requirements.txt`.
> **CRITICAL: use Redis as the storage backend, not the default in-memory storage.**
> The default `MemoryStorage` is per-worker — with 4 Gunicorn workers each worker tracks
> its own counter independently, so the effective limit is 4× the configured value.
> Configure: `limiter = Limiter(storage_uri="redis://localhost:6379")` and add `redis` to
> `requirements.txt`. Add Redis to the EC2 setup steps (install via `apt`, default port).

> ⚠️ **IMPORTANT NOTE [Security #13 — API Key Location]**
> NEVER accept the API key as a query parameter (e.g. `?api_key=...`).
> Query parameters are recorded in server access logs and browser history,
> permanently exposing the secret. Only accept it in the `Authorization` header.

**`GET /auth/login`** — redirect to Google consent screen.

**`GET /auth/callback`** — called by Google after consent:
1. Exchange the auth code for tokens (authlib validates `state` automatically — do not bypass)
2. Extract user info from the token's `userinfo` claim
3. Look up the user in DB by `google_id`
4. If not found, create a new user row with a freshly generated API key (`secrets.token_hex(32)`)
5. Store minimal user info in the Flask session (id, email, name, picture — NOT the Google access token)
6. Redirect to `/`

**`GET /auth/logout`** — clear session, redirect to `/auth/login`.

**`GET /auth/me`** — return current user info + API key:
```
Check if 'user' is in the Flask session manually (NOT @login_required)
If not in session: return {"logged_in": false} as JSON (200 OK, not redirect)
If in session: fetch api_key from DB, return full user JSON with logged_in:true
```

> ⚠️ **IMPORTANT NOTE [Correctness — /auth/me must NOT use @login_required]**
> The frontend calls `GET /auth/me` via `fetch()` on startup to check login state.
> If `@login_required` is applied and the session has expired, Flask returns a `302`
> redirect to `/auth/login`. The browser follows it, returns HTML, and the JSON parse
> crashes the frontend. This endpoint MUST use a manual session check and always return
> JSON — `{"logged_in": false}` when unauthenticated, full user object when authenticated.

**`POST /auth/regenerate-key`** — generate a new API key for the current user (session auth), return it.

**Checkpoint:** Browser login → `/auth/me` returns `{"api_key": "...", "logged_in": true, ...}`.

---

### Phase 3 — Core REST API

**Goal:** A training script can create a run, log data, and finish it. Dashboard can query all data.

#### 3.1 `storage.py` — image saving

**`save_image(data_b64, project_id, run_id, step, key)`**:

1. Check the byte length of the raw base64 data before decoding — reject if too large
2. Decode with `base64.b64decode(data_b64, validate=True)` — the `validate=True` flag rejects strings containing non-base64 characters (returns a clean 400 instead of silently mangling the input)
3. Set `Image.MAX_IMAGE_PIXELS = 50_000_000` **before** calling `Image.open()` — this cap prevents decompression bomb attacks where a small compressed file (valid PNG, fits size check) expands to gigabytes of RAM when decoded. Without this, 4 simultaneous decompression bomb uploads exhaust all worker memory. Then open with PIL to validate it is a real image — this raises an exception for arbitrary binary blobs
4. Convert to RGB (normalises format)
5. Build the storage path from integer IDs only: `{project_id}/{run_id}/{step}_{sanitised_key}.png`
   - Sanitise `key` by keeping only alphanumeric, `-`, and `_` characters
6. Create directories if needed
7. Save as PNG
8. Return the relative path for storage in the DB

> ⚠️ **IMPORTANT NOTE [Security #7 — Image Size Guard + Decompression Bomb]**
> Check the base64 payload length BEFORE decoding. A malicious script can send
> repeated max-size payloads that tie up all Gunicorn workers doing CPU-bound PIL work.
> Reject payloads above a reasonable limit (e.g. 20 MB decoded, ~27 MB base64) immediately.
> Additionally, set `Image.MAX_IMAGE_PIXELS = 50_000_000` before `Image.open()`. The size
> check guards against large uncompressed inputs, but a heavily compressed image (e.g. a
> valid PNG containing a 50,000×50,000 pixel gradient) passes the size check but expands
> to ~7 GB in RAM during decoding. PIL will raise `DecompressionBombError` if the pixel
> count exceeds the cap — catch it and return 400.

> ⚠️ **IMPORTANT NOTE [Security #14 — No User-Controlled Path Components]**
> The storage path MUST be built from DB-issued integer IDs (`project_id`, `run_id`)
> and a character-sanitised key. Never use the raw project name, run name, or image key
> as a path component — they are user-supplied strings and can contain `../` traversal sequences.

#### 3.2 `routes/api.py` — write endpoints (API key auth)

**`POST /api/v1/runs`** — create or resume a run:

```
Validate: project name and run name are non-empty, max 200 chars each
Look up the project for this user (match by user_id + project name)
  If not found: atomically create the project using INSERT OR IGNORE, then fetch it
Look up the run in that project (match by project_id + run name)
  If not found: atomically create the run using INSERT OR IGNORE, then fetch it
Return: run_id and project_id
```

> ⚠️ **IMPORTANT NOTE [Correctness #4 — TOCTOU Race Condition]**
> DO NOT use a SELECT-then-INSERT two-step for project or run creation.
> With 4 Gunicorn workers, two concurrent `POST /runs` calls with the same name will
> both pass the SELECT check, then both try INSERT, causing an IntegrityError.
> Use `INSERT OR IGNORE INTO ... VALUES (...)` followed by a single SELECT.
> This is atomic and safe under concurrent workers.

> ⚠️ **IMPORTANT NOTE [Correctness #16 — Input Validation]**
> Validate that project name and run name are non-empty strings, at most 200 characters.
> Reject with 400 if either is missing or exceeds the limit. Do not silently truncate.
> Also validate `config` if provided: the serialised JSON must be ≤ 64 KB. Reject with 400
> if larger. An uncapped config allows any authenticated user to inflate the DB arbitrarily.

---

**`POST /api/v1/runs/<run_id>/log`** — log one step of scalars and/or images:

```
Parse request body as JSON — if body is not a valid JSON object, return 400
Verify the run exists AND belongs to the authenticated user
  (JOIN through projects to check user_id — do not trust run_id alone)
Verify the run status is 'running'
  If status is 'finished' or 'crashed': return 409 Conflict
    Response body: {"error": "Run is terminated. Call /resume to continue logging."}
Extract step number from payload (default 0)
  Validate step is a non-negative integer, maximum 10,000,000 — reject with 400 otherwise
For each key-value pair in payload (excluding 'step' and 'images'):
  Validate key is a non-empty string, max 200 characters — skip or reject invalid keys
  If value is a number: prepare a metric insert row
Validate 'images' field, if present, is a dict of {string: string} — return 400 otherwise
  Validate number of images ≤ 20 — return 400 if exceeded
  For each key-image pair in images:
    Call save_image(); track each saved path in a list
    Prepare an image insert row
Write all metric rows and image rows in a single DB transaction
  If transaction or any save_image() fails: delete all successfully saved image files, then re-raise
Return: {"ok": true}
```

> ⚠️ **IMPORTANT NOTE [Security #1 — Run Ownership on Log]**
> Always verify that the run's project belongs to the authenticated user before accepting data.
> The run_id comes from the URL and is user-supplied. A valid API key holder must NOT be able
> to log data into another user's run. Query: runs JOIN projects WHERE projects.user_id = g.user_id.

> ⚠️ **IMPORTANT NOTE [Correctness — Status Gate on Log]**
> A terminated run (status = 'finished' or 'crashed') must REFUSE new log data.
> Return 409 Conflict with a clear message directing the caller to use the `/resume` endpoint.
> This prevents stale training scripts from polluting a completed run's data.

> ⚠️ **IMPORTANT NOTE [Correctness — Atomic image save + rollback on failure]**
> Image files are written to disk BEFORE the DB transaction. If saving image #3 fails,
> images #1 and #2 are already on disk but the DB hasn't been written yet — orphaned files.
> Track all successfully saved paths, and on any failure delete them before re-raising.
> See SKILL.md S-47 for the pattern.

---

**`POST /api/v1/runs/<run_id>/finish`** — mark run as finished or crashed:

```
Verify the run exists AND belongs to the authenticated user
  (same JOIN check as /log — do not skip this)
Validate status value is one of: 'finished', 'crashed' — reject with 400 otherwise
If the run is already 'finished' or 'crashed': return 200 {"ok": true} idempotently
  (a script calling finish() twice should not error — e.g. on retry after network failure)
Update the run: set status and finished_at timestamp
Return: {"ok": true}
```

> ⚠️ **IMPORTANT NOTE [Security #1 — Run Ownership on Finish]**
> CRITICAL: the original spec was missing this ownership check.
> Any authenticated user with a valid API key could crash another user's run.
> The ownership JOIN (runs → projects → user_id = g.user_id) is mandatory here,
> exactly as in the /log endpoint.

---

**`POST /api/v1/runs/<run_id>/resume`** — reopen a terminated run for logging:

```
Verify the run exists AND belongs to the authenticated user
If the run status is already 'running': return 409 (already active)
Set status back to 'running', clear finished_at
Return: {"ok": true, "run_id": run_id}
```

> ⚠️ **IMPORTANT NOTE [Design — Resume API]**
> This endpoint is required to allow intentional continuation of a finished/crashed run.
> Without it, the status gate on /log would permanently lock out any terminated run.
> Only the owning user can resume their own run. The client SDK exposes this as `run.resume()`.
> The run's existing metrics and images are kept — resume does NOT reset data.

---

#### 3.3 `routes/projects.py` — project CRUD (session auth)

**`GET /api/v1/projects`** — list all projects for the logged-in user, ordered newest first.

**`DELETE /api/v1/projects/<project_id>`** — delete a project:
```
Verify the project belongs to the current session user
  (WHERE id=? AND user_id=? — do not rely on user_id alone)
Delete the project row
  (cascade automatically removes all runs, metrics, and image DB rows)
If rowcount == 0: return 404 (not found or not owned — never silent 200)
Delete the project's image directory from disk: data/files/<project_id>/
Return: {"ok": true}
```

> ⚠️ **IMPORTANT NOTE [Correctness #2 — Cascade Required]**
> The DELETE only works without errors if `ON DELETE CASCADE` is on the schema.
> Without CASCADE, SQLite (with foreign keys ON) will raise a constraint error
> because runs still reference the project being deleted.

> ⚠️ **IMPORTANT NOTE [Operations — Delete image files from disk]**
> Cascade only removes DB rows. The image files in `data/files/<project_id>/` remain on disk
> forever, eventually filling the EBS volume. After the DB DELETE succeeds, call
> `shutil.rmtree(project_dir, ignore_errors=True)` to remove the directory.
> `ignore_errors=True` handles projects that had no images without raising an error.

---

#### 3.4 `routes/runs.py` — run CRUD + data query (session auth)

**`GET /api/v1/projects/<project_id>/runs`** — list runs in a project:
```
Verify the project belongs to the current user (JOIN check)
Return all runs ordered newest first
```

**`GET /api/v1/runs/<run_id>`** — get run metadata and config:
```
Verify the run belongs to the current user (JOIN check)
Parse the 'config' column with json.loads() before including in the response
  If json.loads() raises (malformed stored value): return config: null, do not 500
Return run row as JSON
```

**`GET /api/v1/runs/<run_id>/metric-keys`** — list all distinct metric key names for a run:
```
Verify run ownership (JOIN check — same as all other run endpoints)
Return list of distinct key strings
```

> ⚠️ **IMPORTANT NOTE [Security — Ownership on metric-keys]**
> The ownership JOIN is REQUIRED here even though this is a "harmless" listing endpoint.
> Without it, any authenticated user can enumerate the metric names of any run by guessing
> run_id values (e.g. brute-forcing IDs 1, 2, 3 ...). Key names may reveal model architecture,
> experiment design, or proprietary information.

**`GET /api/v1/runs/<run_id>/metrics?keys=loss,acc&max_points=1000`** — fetch metric time series:
```
Verify run ownership
If keys param is provided: filter to those keys using parameterised placeholders
  (build: WHERE key IN (?, ?, ...))
Read max_points param (default 1000, clamp to [1, 5000])
For each key:
  Count total rows
  If count > max_points: downsample by selecting every Nth row (N = count // max_points)
    Use: SELECT ... WHERE (id - min_id) % N = 0 ORDER BY step
  Else: return all rows
Group results by key: return {key: [{step, value}, ...]}
```

> ⚠️ **IMPORTANT NOTE [Performance — Metrics pagination]**
> A run logging 1,000 steps/epoch × 100 epochs = 100,000 rows per metric key.
> With 10 metric keys that is 1,000,000 rows in a single response — the browser will
> time out and Chart.js will freeze. The `max_points` downsampling cap is mandatory.
> Return a `"downsampled": true` flag in the response so the UI can show a notice.

> ⚠️ **IMPORTANT NOTE [Security #5 — Dynamic IN Clause]**
> When building the `IN (?, ?, ...)` clause, generate the placeholders programmatically
> from the list length — do NOT format them into the SQL string.
> Example: `','.join('?' * len(key_list))` inserted into the query template, then
> pass the actual values as parameters. String-formatting key names into SQL is injection.

**`GET /api/v1/runs/<run_id>/image-keys`** — list all distinct image key names for a run:
```
Verify run ownership (JOIN check)
Return list of distinct key strings
```

> ⚠️ **IMPORTANT NOTE [Security — Ownership on image-keys]**
> Same as metric-keys above — the ownership check is mandatory. Image key names reveal
> what inputs/outputs a model was visualising, which is proprietary information.

**`GET /api/v1/runs/<run_id>/images?key=input`** — fetch image step list:
```
Verify run ownership
If 'key' query parameter is absent or empty: return 400
Return [{step, url: "/files/<path>"}] ordered by step, capped at 500 entries
  (if more than 500 exist, return the last 500 steps so the most recent are always visible)
Include "total": N in the response so the UI can show "showing last 500 of N"
```

**`DELETE /api/v1/runs/<run_id>`** — delete a run:
```
Verify the run belongs to the current user (via project ownership JOIN)
Fetch the run's project_id before deleting (needed for disk path)
Delete the run row
  (cascade removes all metrics and image DB rows for that run)
If rowcount == 0: return 404
Delete the run's image directory from disk: data/files/<project_id>/<run_id>/
Return: {"ok": true}
```

> ⚠️ **IMPORTANT NOTE [Security — 404 on ownership mismatch for DELETE]**
> Applies to BOTH `DELETE /projects/<id>` and `DELETE /runs/<id>`.
> The WHERE clause filters by user ownership. If the resource belongs to another user
> (or doesn't exist), SQLite deletes 0 rows silently — the handler returns 200 {"ok":true}
> without error. This is wrong: it masks the fact that the target resource exists but
> was not deleted. Always check `cursor.rowcount` after DELETE:
> if 0, return 404. This also prevents probing which IDs exist across user boundaries.

**Checkpoint:** Use `curl` (see Part 2) to log a run and query metrics — all return correct JSON.

---

### Phase 4 — Frontend (Vue 3 SPA)

**Goal:** The dashboard loads, shows the project/run tree, and renders live charts.

#### 4.1 `frontend/index.html` — shell

The HTML file contains only:
- The `<title>`, font-awesome CSS link, and `style.css` link
- An import map that maps `"vue"` and `"chart.js"` to their CDN ESM URLs (unpkg)
- A single `<div id="app">` mount point
- A `<script type="module" src="app.js">` tag

No inline JavaScript. No CDN script tags outside the import map.

> ⚠️ **IMPORTANT NOTE [Security — CDN Subresource Integrity]**
> Every external URL in the import map and every CDN `<link>` tag MUST include a
> `integrity="sha384-<hash>"` attribute and `crossorigin="anonymous"`.
> Without SRI, a compromised CDN (e.g. unpkg.com) can serve malicious JavaScript that
> runs with full page access — stealing the user's API key and session cookie.
> Generate the hash for each versioned URL before pinning it (see SKILL.md S-45).
> Pin exact versions (e.g. `vue@3.4.21`) — never use `vue@3` (resolves to latest, changes).
>
> **Browser support caveat:** SRI on import map module specifiers (the URLs inside
> `<script type="importmap">`) requires Chrome 112+ and Firefox 126+. Older browsers
> silently load the modules WITHOUT verifying the hash. The `<link>` tags for CSS (Font
> Awesome) and the Font Awesome `<script>` outside the import map have full SRI support
> everywhere. Document the minimum supported browser versions in the project README.

#### 4.2 `frontend/style.css` — layout and theming

Defined as CSS custom properties on `:root` (dark defaults) and overridden on `body.light`.

Layout: CSS Grid with three named areas — `topbar`, `left + lhandle + main`, `status`.
The left panel width is controlled by `--left-w` (default 240px), updated live during drag.

Key component styles:
- `.tree-item` — flex row; trash icon hidden by default, revealed on `:hover`
- `.tree-item.selected` — accent left border + `--sel-bg` background
- `.chart-card` — panel background, rounded border, fixed canvas height (220px)
- `.img-slider` — panel background, image fills width up to 400px height
- `.status-dot` — small coloured circle: green=running, grey=finished, red=crashed
- `button:hover` — always `#2563eb` (blue-600), white text; never uses a theme variable

#### 4.3 `frontend/app.js` — Vue 3 SPA

All components live in this single file using `defineComponent` / `createApp`.

**Component tree:**
```
App
├── TopBar        (logo, user avatar, API key copy, theme toggle, logout)
├── LeftPanel     (project/run collapsible tree)
│   ├── ProjectRow
│   └── RunRow
├── MainPanel     (dashboard area)
│   ├── MetricChart  (one Chart.js line chart per metric key)
│   └── ImageSlider  (one slider per image key)
└── StatusBar     (selected item info + counts)
```

**Root reactive state (inside App `setup()`):**
- `projects` — array of project objects, each with a nested `runs` array
- `sel_project` — the currently selected project object (or null)
- `sel_run` — the currently selected run object (or null)
- `dash_data` — `{metrics: {key: [{step, value}]}, image_keys: []}` for the current selection
- `is_loading` — boolean flag to show loading indicators
- `user` — `{name, email, picture, api_key}` from `/auth/me`

**Data flow on startup:**
1. Call `GET /auth/me` — if `logged_in` is false, redirect to `/auth/login`
2. Call `GET /api/v1/projects` — populate the project list
3. For each project, call `GET /api/v1/projects/<id>/runs` — attach runs to the project

> ⚠️ **IMPORTANT NOTE [Performance — N+1 requests on startup and project select]**
> Startup fires 1 + N requests (1 projects call + 1 per project for runs).
> On project select, it fires M (metric-keys per run) + K (metrics per key) requests.
> For 20 projects with 10 runs and 8 metric keys: startup = 21 requests, project select = 18.
> This is acceptable for small-scale personal use. If the tree grows large, consider adding
> a `GET /api/v1/projects?include_runs=true` batch endpoint to collapse startup to 1 request.

**On project selected:**
- For each run in the project, fetch all metric keys
- Union all keys across runs; for each key fetch all runs' time series
- Render one multi-line chart per key (one dataset per run, coloured by run index)
- Do NOT show image sliders in project view

**On run selected:**
- Fetch metric keys → fetch time series for each → render single-run charts
- Fetch image keys → render one image slider per key
- If run status is `running`: start a 5-second auto-refresh interval with exponential backoff on consecutive fetch failures (5s → 10s → 20s → 40s → max 60s; reset to 5s on next success)
- When a different run/project is selected: **clear the interval before starting a new one**

> ⚠️ **IMPORTANT NOTE [Frontend #11 — Interval Leak]**
> The auto-refresh `setInterval` MUST be stored in a variable and cleared with
> `clearInterval` every time the selection changes or the component unmounts.
> Failing to do so causes multiple overlapping intervals to accumulate as the user
> clicks around the tree, creating escalating API request storms.

**Chart colour per run index:**
- Use a golden-angle hue spread: `hsl((index * 137) % 360, 65%, 55%)`
- This gives visually distinct colours without manual assignment

**Chart.js integration:**
- Destroy the existing chart instance before creating a new one on data change
- Read colour values from CSS custom properties at chart creation time (for theme support)
- Use `animation: false` for live-updating charts to prevent re-animation on each append

**Drag-resize handle:**
- Mouse-down on `.lhandle` starts a resize: capture `start_x` and `start_w`
- Mouse-move updates `--left-w` CSS variable: clamp between 160px and 480px
- Mouse-up removes both listeners

---

### Phase 5 — Client SDK (`wandb_clone.py`)

**Goal:** A one-file Python SDK that training scripts can use with no extra dependencies beyond `requests`.

The SDK exposes two things: `init()` function and the `Run` object it returns.

**`init(project, name, api_key, host, config)`:**
```
POST /api/v1/runs with project name, run name, and optional config dict
On success: create and return a Run object holding host, api_key, run_id, project_id
```

**`Run.log(data, step)`:**
```
If step is not provided: use internal auto-incrementing counter
Separate data into scalars (numbers) and images (PIL Image or numpy array)
For each image value:
  If numpy array: convert to PIL Image first
  Encode PIL Image as PNG into memory buffer
  Base64-encode the buffer bytes
Build payload: {step, scalar_key: value, ..., images: {key: base64_string, ...}}
POST /api/v1/runs/<run_id>/log with the payload and Bearer header
Raise on HTTP error
```

**`Run.finish(status)`:**
```
POST /api/v1/runs/<run_id>/finish with status ('finished' or 'crashed')
```

**`Run.resume()`:**
```
POST /api/v1/runs/<run_id>/resume with Bearer header
On success: the run is back in 'running' state and log() will be accepted again
```

> ⚠️ **IMPORTANT NOTE [SDK — Resume Workflow]**
> The SDK's `resume()` method is needed when a script wants to continue an existing
> run that was previously finished or crashed. After calling `resume()`, subsequent
> `log()` calls will be accepted by the server. Without calling `resume()` first,
> the server returns 409 and the SDK should raise a clear exception explaining this.

---

## Part 2 — Test Plan

### 2.1 Test structure

All tests live in `backend/tests/`. Use `pytest` + `pytest-flask`.

```
backend/tests/
├── conftest.py        App fixture with temp DB and files dir; seeded user + API key
├── test_auth.py       OAuth flow + API key validation
├── test_api.py        Log endpoint — scalars, images, errors, ownership, status gate
├── test_projects.py   Project CRUD + cascade delete
├── test_runs.py       Run CRUD + metrics + images query + ownership
└── test_storage.py    Base64 decode, path sanitisation, size guard
```

#### Test fixtures (`conftest.py`)

The test app uses a `TestConfig` with:
- A temporary DB file (unique per test via `tmp_path`)
- A temporary files directory
- Fake Google credentials (`'fake'` strings — OAuth flow is not tested end-to-end)
- `SESSION_COOKIE_SECURE = False`

The `api_key` fixture seeds a user directly into the test DB and returns their API key.
This bypasses the OAuth flow so API tests can run without Google.

#### What to test in `test_api.py`

- Creating a run succeeds and returns `run_id`
- Creating a run with the same name twice returns the same `run_id` (idempotent)
- Logging scalars to a valid run succeeds
- Logging to a run owned by a different user returns 404
- Logging to a finished run returns 409
- Logging to a run after `resume` succeeds again
- Finishing a run with an invalid status returns 400
- Finishing another user's run returns 404
- Missing or invalid API key returns 401
- Image payloads above the size limit are rejected

#### What to test in `test_projects.py`

- Listing projects returns only the current user's projects
- Deleting a project also deletes its runs and metrics (cascade)

#### What to test in `test_runs.py`

- Listing runs returns only runs in a project the user owns
- Querying metrics returns correctly grouped time series
- Querying with `?keys=` filter works with parameterised placeholders
- Deleting a run also deletes its metrics and images (cascade)

#### What to test in `test_storage.py`

- Valid base64 PNG is decoded, validated, and saved correctly
- Invalid binary data (not an image) raises an error before writing to disk
- Payloads above the size limit are rejected before decoding
- Key sanitisation removes all special characters from the filename

---

### 2.2 Manual API testing walkthrough

#### Step 1 — Start the server
```bash
cd backend
pip install -r requirements.txt
flask --app app run --port 5000 --debug
```

#### Step 2 — Log in and get your API key
1. Open `http://localhost:5000/auth/login` in a browser and complete Google login.
2. Visit `http://localhost:5000/auth/me` to see your user JSON — copy the `api_key` value.
3. Export it: `KEY="<your-api-key>"`

#### Step 3 — Create a project and run
```bash
curl -X POST http://localhost:5000/api/v1/runs \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"project":"mnist","name":"run-001","config":{"lr":0.001}}'
```
Expected: `{"run_id": 1, "project_id": 1}`
Set: `RUN_ID=1`

#### Step 4 — Log scalars for several steps
Send one POST per step with a `step` number and scalar values.
Expected response each time: `{"ok": true}`

#### Step 5 — Finish the run
```bash
curl -X POST http://localhost:5000/api/v1/runs/$RUN_ID/finish \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"status":"finished"}'
```

#### Step 6 — Verify that logging to a finished run is rejected
Attempt another log POST to the same run_id.
Expected: `409 Conflict` with body `{"error": "Run is terminated. Call /resume to continue logging."}`

#### Step 7 — Resume the run and log again
```bash
curl -X POST http://localhost:5000/api/v1/runs/$RUN_ID/resume \
  -H "Authorization: Bearer $KEY"
```
Then send another log POST — it should succeed with 200.

#### Step 8 — Query metrics via browser
While logged in, open these in the browser:
- `/api/v1/projects` — list projects
- `/api/v1/projects/1/runs` — list runs
- `/api/v1/runs/1/metrics` — all metrics
- `/api/v1/runs/1/metrics?keys=loss` — filtered

#### Step 9 — Test the SDK end-to-end
Run the Python SDK script (see `wandb_clone.py`) pointing at `http://localhost:5000`.
Use your API key. Open the dashboard and verify the run and charts appear.

#### Step 10 — Frontend smoke test
Open `http://localhost:5000`. Verify:
- [ ] Project appears in left panel
- [ ] Click project → multi-run overlay charts appear
- [ ] Click run → single-run charts + status bar updates
- [ ] Theme toggle works (dark ↔ light)
- [ ] API key button copies to clipboard
- [ ] Drag the resize handle — left panel resizes smoothly

---

### 2.3 Image logging test

Use the SDK to log PIL Images alongside scalars for several steps.
Open the dashboard, select the run, and verify:
- An image slider appears for each image key
- Step forward/backward buttons advance through frames
- Images display correctly in both dark and light themes

---

## Part 3 — Deployment Plan (AWS EC2)

### 3.1 Prerequisites

- AWS account with EC2 access
- Domain name with an A record pointing to the EC2 public IP
- Google OAuth credentials with the production redirect URI registered:
  `https://yourdomain.com/auth/callback`

### 3.2 EC2 Instance Setup

**Recommended instance:** `t3.small` (2 vCPU, 2 GB RAM) for personal/team use.
**OS:** Ubuntu 22.04 LTS.
**Storage:** 20 GB root + a dedicated EBS volume (20+ GB) for `data/`.

**Security group inbound rules:**

| Port | Protocol | Source |
|------|----------|--------|
| 22   | TCP      | Your IP only |
| 80   | TCP      | 0.0.0.0/0 |
| 443  | TCP      | 0.0.0.0/0 |

#### Step 1 — SSH into instance
```bash
ssh -i your-key.pem ubuntu@<ec2-public-ip>
```

#### Step 2 — Install system packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx git
```

#### Step 3 — Mount the EBS data volume
- Use `lsblk` to identify the device name (commonly `/dev/xvdf` or `/dev/nvme1n1`)
- Format the volume with ext4 (only on first use — this erases it)
- Mount it to `/mnt/wandb_data`
- Add an `/etc/fstab` entry with the `nofail` option so a missing volume does not block boot
- Create the `files/` subdirectory on the volume

#### Step 4 — Clone the repo and install Python dependencies
```bash
cd /home/ubuntu
git clone https://github.com/youruser/wandb_clone.git
cd wandb_clone/backend
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Step 5 — Production environment variables

> ⚠️ **IMPORTANT NOTE [Security #6 — Secret Storage]**
> Do NOT store secrets in `/etc/environment` — that file is world-readable by default.
> Instead, create a dedicated secrets file owned by root with mode 600,
> and reference it via the systemd `EnvironmentFile=` directive.
> This means only root and the service process can read the secrets.

Create `/etc/wandb_clone.env` (as root, mode 600):
```
SECRET_KEY=<generate with: python3 -c 'import secrets; print(secrets.token_hex(32))'>
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
DB_PATH=/mnt/wandb_data/wandb.db
FILES_DIR=/mnt/wandb_data/files
SESSION_COOKIE_SECURE=true
```

Set permissions:
```bash
sudo chmod 600 /etc/wandb_clone.env
sudo chown root:root /etc/wandb_clone.env
```

#### Step 6 — Gunicorn config (`gunicorn.conf.py`)

Settings:
- `bind = "127.0.0.1:8000"` — only accept connections from localhost (Nginx proxies to this)
- `workers = 4` — 2× CPU cores; sync worker class
- `worker_class = "sync"` — safe with SQLite WAL; do not use `gevent` or `eventlet`
- `timeout = 120` — generous for image uploads
- `accesslog` and `errorlog` pointed at `/var/log/gunicorn/`

Create the log directory:
```bash
sudo mkdir -p /var/log/gunicorn
sudo chown ubuntu:ubuntu /var/log/gunicorn
```

#### Step 7 — Systemd service

Create `/etc/systemd/system/wandb_clone.service`:
- `User=ubuntu`
- `WorkingDirectory` → `backend/` inside the repo
- `EnvironmentFile=/etc/wandb_clone.env` — loads the secrets file
- `ExecStart` → the venv gunicorn binary with `-c gunicorn.conf.py "app:create_app()"`
- `Restart=on-failure`
- `PrivateTmp=true`

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable wandb_clone
sudo systemctl start wandb_clone
sudo systemctl status wandb_clone   # must show 'active (running)'
```

#### Step 8 — Nginx config

Create `/etc/nginx/sites-available/wandb_clone`:
- Serve `frontend/` as static files for `/` with `try_files $uri $uri/ /index.html` (SPA fallback)
- Proxy `/(api|auth|files|health)/` to `http://127.0.0.1:8000`
- Set proxy headers: `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`
- Set `client_max_body_size 20m` — large enough for image uploads but not unlimited

Enable the site, test config, reload Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/wandb_clone /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### Step 9 — HTTPS with Let's Encrypt
```bash
sudo certbot --nginx -d yourdomain.com
```
Certbot auto-modifies the Nginx config to redirect HTTP → HTTPS and sets up auto-renewal.

After this, verify `SESSION_COOKIE_SECURE=true` is in the env file (Step 5).
Update the Google OAuth console to add the HTTPS redirect URI.

---

### 3.3 Deployment Verification Checklist

Run these checks in order after deployment:

1. **Service health:** `sudo systemctl status wandb_clone` shows `active (running)`.
   Confirm: `curl http://127.0.0.1:8000/health` from the EC2 instance returns `{"status":"ok"}`.

2. **Nginx proxy:** `curl https://yourdomain.com/health` from your local machine returns `{"status":"ok"}`.

3. **Browser login:** Open `https://yourdomain.com` — should redirect to Google, then land on the dashboard.
   Visit `/auth/me` to confirm user JSON is returned.

4. **Remote SDK test:** From your local machine, run the SDK against `https://yourdomain.com`.
   Log 20 steps of scalars. Open the dashboard and confirm the run and charts appear.

5. **Concurrency test:** Run two SDK scripts simultaneously (two terminal windows), each logging to a different run in the same project. Both should complete without errors. Both runs should appear on the dashboard simultaneously with distinct chart lines.

6. **Ownership test:** Use two different user accounts (or the same account with two API keys before one is regenerated). Confirm that logging to a run_id owned by a different user returns 404, not 200.

---

### 3.4 Maintenance

#### Update code
```bash
cd /home/ubuntu/wandb_clone && git pull
cd backend && source venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart wandb_clone
sudo systemctl reload nginx
```

#### View logs
```bash
sudo journalctl -u wandb_clone -f            # gunicorn startup errors
tail -f /var/log/gunicorn/access.log          # request log
tail -f /var/log/gunicorn/error.log           # app errors
sudo tail -f /var/log/nginx/error.log         # nginx errors
```

#### Backup the database
SQLite WAL mode supports hot backups. Run:
```bash
sqlite3 /mnt/wandb_data/wandb.db ".backup '/mnt/backup/wandb_$(date +%Y%m%d).db'"
```

Schedule with cron (runs at 3 AM daily):
```
0 3 * * * sqlite3 /mnt/wandb_data/wandb.db ".backup '/mnt/backup/wandb_$(date +\%Y\%m\%d).db'"
```

> ⚠️ **IMPORTANT NOTE [Operations #14 — Backup Target + Retention]**
> `/mnt/backup` MUST be a **separate EBS volume** (or an S3 mount via `s3fs` / `aws s3 cp`),
> NOT a directory on the same `/mnt/wandb_data` volume. If the data volume fails or is
> accidentally deleted, backups on the same volume are lost with it.
> Recommended: use `aws s3 cp` to push the backup file to an S3 bucket after each run.
> Also add a cleanup step to delete backups older than 30 days:
> `find /mnt/backup -name 'wandb_*.db' -mtime +30 -delete`

#### Log rotation
Create `/etc/logrotate.d/gunicorn`:
- Rotate `/var/log/gunicorn/*.log` daily
- Keep 14 days of compressed logs
- Use `copytruncate` (Gunicorn holds the file open)

> ⚠️ **IMPORTANT NOTE [Operations #13 — Log Rotation]**
> Without logrotate, Gunicorn access and error logs grow indefinitely.
> On a busy server, this will fill the root volume. Configure logrotate before going live.

---

## Part 4 — Implementation Order Checklist

```
[ ] Phase 1: config.py + db.py + app.py
      → test: GET /health returns 200

[ ] Phase 2: auth.py + Google OAuth credentials in .env
      → test: browser login flow + /auth/me returns user + api_key

[ ] Phase 3a: storage.py + routes/api.py (create_run, log_step, finish_run, resume_run)
      → test: curl create + log + finish + 409 on log after finish + 200 after resume

[ ] Phase 3b: routes/projects.py + routes/runs.py
      → test: curl query cycle (projects, runs, metrics, images)

[ ] Phase 3c: run pytest — all tests green
      → verify ownership tests and status-gate tests pass

[ ] Phase 4: frontend/index.html + style.css + app.js
      → test: dashboard shows charts + sliders for a logged run; interval cleared on selection change

[ ] Phase 5: wandb_clone.py SDK (init, log, finish, resume)
      → test: end-to-end script → dashboard shows data; resume() works after finish()

[ ] Deployment: EC2 + Gunicorn + Nginx + HTTPS
      → test: all items in §3.3 Deployment Verification Checklist

[ ] Update log.md, PROGRAM_update.md, SKILL.md
[ ] git commit
```
