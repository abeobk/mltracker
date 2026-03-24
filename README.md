# MLTracker

A self-hosted experiment tracking platform for machine learning — log scalar metrics and images from your training scripts and visualise them in real time through a web dashboard.

Think Weights & Biases, but running entirely on your own server with no external dependencies, no data leaving your machine, and no usage limits.

---

## Features

- **Real-time dashboard** — charts and images update every 5 seconds while a run is in progress
- **Multi-run overlay** — compare runs on the same chart automatically; image cards show all runs side by side
- **Drag & resize cards** — rearrange and resize every chart/image card; layout is saved per project/run
- **Image logging** — log numpy arrays or PIL images at any step; browse them with a step slider
- **Google OAuth login** — browser sign-in via Google; scripts authenticate with a per-user API key
- **Admin dashboard** — first registered user sees a user stats table (projects, runs, total tracking time)
- **Dark / light theme** — toggle in the top bar
- **Simple Python SDK** — one file, one dependency (`requests`), async logging with automatic batching

---

## Architecture

```
Browser (Vue 3 SPA)
        │  HTTPS
        ▼
     Nginx  ─── static frontend files ──→  frontend/
        │
        │  proxy /api /auth /files /health
        ▼
   Gunicorn (4 workers)
        │
    Flask app
        ├── SQLite  (metadata — users, projects, runs)
        ├── JSONL   (metrics + image refs, one file per run)
        └── Filesystem  (PNG images, one dir per run)
```

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask / Gunicorn |
| Database | SQLite (WAL mode) |
| Auth (browser) | Google OAuth 2.0 |
| Auth (scripts) | Per-user API key (Bearer token) |
| Rate limiting | flask-limiter + Redis |
| Frontend | Vue 3 (CDN ESM), Chart.js 4, Font Awesome 6 |
| Deployment | AWS EC2, Nginx, systemd, Let's Encrypt |

---

## Quick Start (local dev)

**Prerequisites:** Python 3.11+, Redis running locally, a Google OAuth app.

```bash
git clone https://github.com/abeobk/mltracker.git
cd mltracker

# Backend
cd backend
python3.11 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Set required env vars (or copy and fill in the template)
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
export GOOGLE_CLIENT_SECRET=<your-client-secret>
export DB_PATH=../data/mltracker.db
export FILES_DIR=../data/mltracker

flask --app app run --debug        # http://localhost:5000
```

Open `http://localhost:5000` in your browser and sign in with Google.

### Log your first run

```bash
pip install requests pillow numpy   # SDK dependencies
export WANDB_API_KEY=<your-api-key> # copy from the top bar after login
export WANDB_HOST=http://localhost:5000

python sinewave_test.py             # demo script included in the repo
```

---

## Python SDK

```python
import mltracker as tracker

run = tracker.init(project="mnist", name="exp1", config={"lr": 0.001, "epochs": 10})

for step in range(100):
    run.log({"loss": 0.5 / (step + 1), "acc": 1 - 0.4 / (step + 1)}, step=step)

    if step % 10 == 0:
        import numpy as np
        img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        run.log({"pred": tracker.Image(img)}, step=step)

run.finish()
```

**Resuming a run:**
```python
run = tracker.resume(project="mnist", name="exp1_a3f2b1")
run.log({"loss": 0.01}, step=100)
run.finish()
```

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `WANDB_API_KEY` | — | Required. Copy from the dashboard top bar. |
| `WANDB_HOST` | `http://localhost:5000` | URL of your MLTracker server. |

---

## Deployment on AWS EC2

### 1. Launch an EC2 instance

- **AMI:** Ubuntu 22.04 LTS
- **Instance type:** `t3.small` (or larger)
- **Storage:** 20 GB root volume + a separate EBS volume for data (recommended: 20–100 GB)
- **Security group inbound rules:**
  - Port 22 — your IP only
  - Port 80 — anywhere (0.0.0.0/0)
  - Port 443 — anywhere (0.0.0.0/0)

### 2. Set up Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Web application)
3. Add authorised redirect URI: `https://yourdomain.com/auth/callback`
4. Note the **Client ID** and **Client Secret**

### 3. Bootstrap the server

SSH into the instance, clone the repo, and run the bootstrap script:

```bash
ssh ubuntu@<your-ec2-ip>
git clone https://github.com/abeobk/mltracker.git ~/mltracker
sudo bash ~/mltracker/setup/bootstrap.sh
```

The script will:
- Install Python 3.11, Nginx, Redis, Certbot, and dependencies
- Detect and format the secondary EBS volume (mounts at `/mnt/mltracker_data`)
- Create a Python virtualenv and install pip dependencies
- Install the systemd service and configure Nginx
- Prompt you for your domain name

### 4. Fill in secrets

```bash
sudo nano /etc/mltracker.env
```

```ini
SECRET_KEY=<generate: python3 -c "import secrets; print(secrets.token_hex(32))">
GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-client-secret>
DB_PATH=/mnt/mltracker_data/mltracker.db
FILES_DIR=/mnt/mltracker_data/mltracker
SESSION_COOKIE_SECURE=false    # set to true after HTTPS is working
```

### 5. Start the service

```bash
sudo systemctl start mltracker
sudo systemctl status mltracker

# Quick health check
curl http://localhost:8000/health
```

### 6. Point DNS and enable HTTPS

Add an A record for `yourdomain.com` pointing to your EC2 Elastic IP, then:

```bash
sudo bash ~/mltracker/setup/certbot.sh
```

This runs Certbot, installs the TLS certificate, flips `SESSION_COOKIE_SECURE=true`, and restarts the service. Auto-renewal is configured automatically.

### Deploying updates

```bash
sudo bash ~/mltracker/setup/update.sh
```

Pulls the latest code, syncs Python dependencies, and restarts Gunicorn.

---

## Project Structure

```
mltracker/
├── backend/
│   ├── app.py              Flask app factory
│   ├── auth.py             Google OAuth + API key middleware
│   ├── config.py           Configuration (reads from env vars)
│   ├── db.py               SQLite helpers + schema
│   ├── storage.py          File save / delete helpers
│   ├── models.py           Dataclass helpers
│   ├── routes/
│   │   ├── api.py          Write API (log scalars & images)
│   │   ├── projects.py     Project CRUD
│   │   ├── runs.py         Run CRUD + data retrieval
│   │   └── admin.py        Admin user stats
│   └── requirements.txt
├── frontend/
│   ├── index.html          HTML shell + CDN import maps
│   ├── style.css           Layout and theme
│   └── app.js              Vue 3 app (all components)
├── setup/
│   ├── bootstrap.sh        One-time server setup
│   ├── certbot.sh          HTTPS / Let's Encrypt setup
│   ├── update.sh           Deploy code updates
│   ├── env.template        Secrets file template
│   └── mltracker.service   systemd unit file
├── mltracker.py            Python SDK (single file, pip install requests)
├── sinewave_test.py        Demo script
├── gunicorn.conf.py        Gunicorn configuration
└── nginx.conf              Reference Nginx config
```

---

## Security Notes

- API keys are transmitted only in the `Authorization: Bearer` header — never in query strings
- Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` in production
- Every API endpoint verifies run ownership via a JOIN — a valid key cannot access another user's data
- Images are validated for size, format, and pixel count before saving
- All SQL uses parameterised queries
- Secrets live in `/etc/mltracker.env` (mode 600, root-owned), loaded via systemd `EnvironmentFile`

---

## License

MIT
