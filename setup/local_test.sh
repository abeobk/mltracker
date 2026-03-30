#!/usr/bin/env bash
# =============================================================================
# local_test.sh — Run MLTracker locally for development / testing
#
# Usage:
#   bash setup/local_test.sh
#
# What it does:
#   - Creates a Python venv and installs backend deps (first run only)
#   - Starts Flask dev server on http://localhost:5000
#   - Uses in-memory rate limiting (no Redis required)
#   - Uses local data/ directory for DB and files
#
# Local-only behaviours:
#   - Google OAuth is disabled (fake credentials) — use email/password only
#   - The first user who registers and logs in auto-activates as admin
#     (bootstrap mechanism: MIN(id) user is always activated on login)
#   - SESSION_COOKIE_SECURE is off (HTTP is fine locally)
#   - Flask debug mode is on (auto-reload on code changes, detailed tracebacks)
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
VENV_DIR="$BACKEND_DIR/venv"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Venv ──────────────────────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating Python venv..."
    python3 -m venv "$VENV_DIR"
fi

info "Installing / syncing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"

# ── Data directories ──────────────────────────────────────────────────────────
mkdir -p "$REPO_DIR/data"

# ── Build SDK wheel ───────────────────────────────────────────────────────────
DOWNLOADS_DIR="$REPO_DIR/frontend/downloads"
mkdir -p "$DOWNLOADS_DIR"
info "Building SDK wheel..."
"$VENV_DIR/bin/pip" install --quiet --upgrade build setuptools wheel
BUILD_TMP=$(mktemp -d)
if "$VENV_DIR/bin/python" -m build --wheel --no-isolation --outdir "$BUILD_TMP" "$REPO_DIR/sdk" &>/dev/null; then
    rm -f "$DOWNLOADS_DIR"/mltracker-*.whl
    cp "$BUILD_TMP"/mltracker-*.whl "$DOWNLOADS_DIR/"
    WHL_NAME=$(ls "$DOWNLOADS_DIR"/mltracker-*.whl | xargs basename)
    ln -sf "$WHL_NAME" "$DOWNLOADS_DIR/mltracker-latest.whl"
    info "SDK wheel: frontend/downloads/$WHL_NAME"
else
    warn "SDK wheel build failed — SDK download button will not work."
fi
rm -rf "$BUILD_TMP"

# ── Environment ───────────────────────────────────────────────────────────────
# SECRET_KEY: required by Flask (raises KeyError at startup if missing)
export SECRET_KEY="local-dev-secret-NOT-for-production"

# Use in-memory rate limiting — no Redis required locally.
# (In production REDIS_URL points at a real Redis instance.)
export REDIS_URL="memory://"

# Keep cookies working over plain HTTP
export SESSION_COOKIE_SECURE="false"

# Flask dev-server settings
export FLASK_APP="app:create_app"
export FLASK_DEBUG="1"

# ── Info ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  MLTracker — local dev server${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Dashboard : http://localhost:5000"
echo "  Register  : http://localhost:5000/auth/register"
echo "  DB        : $REPO_DIR/data/mltracker.db"
echo ""
warn "Google OAuth is disabled (fake credentials). Use email/password."
warn "First user to register and log in auto-activates as admin."
echo ""

# ── Start ─────────────────────────────────────────────────────────────────────
cd "$BACKEND_DIR"
exec "$VENV_DIR/bin/flask" run --host=127.0.0.1 --port=5000
