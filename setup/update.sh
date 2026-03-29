#!/usr/bin/env bash
# =============================================================================
# update.sh — Deploy code updates to a running MLTracker server
#
# Usage:
#   sudo bash /path/to/mltracker/setup/update.sh
#
# What it does:
#   - Pulls latest code from git
#   - Installs any new Python dependencies
#   - Restarts the MLTracker service
#   - Prints recent logs to confirm healthy start
# =============================================================================

set -euo pipefail

# Resolve repo root from the script's own location (works wherever it is cloned)
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$REPO_DIR/backend/venv"
REPO_USER="$(stat -c '%U' "$REPO_DIR")"

GREEN='\\033[0;32m'; YELLOW='\\033[1;33m'; RED='\\033[0;31m'; NC='\\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0"
info "Using repo at $REPO_DIR (owner: $REPO_USER)"

# ── Pull latest code ──────────────────────────────────────────────────────────
info "Pulling latest code..."
sudo -u "$REPO_USER" git -C "$REPO_DIR" pull --ff-only

# ── Update Python dependencies ────────────────────────────────────────────────
info "Updating Python dependencies..."
sudo -u "$REPO_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$REPO_USER" "$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/backend/requirements.txt"

# ── Rebuild SDK wheel and publish to frontend/downloads/ ─────────────────────
info "Building SDK wheel..."
DOWNLOADS_DIR="$REPO_DIR/frontend/downloads"
mkdir -p "$DOWNLOADS_DIR"
sudo -u "$REPO_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade build setuptools wheel
BUILD_TMP=$(sudo -u "$REPO_USER" mktemp -d)
if sudo -u "$REPO_USER" "$VENV_DIR/bin/python" -m build --wheel --no-isolation --outdir "$BUILD_TMP" "$REPO_DIR/sdk" 2>&1 | grep -v "^$"; then
    rm -f "$DOWNLOADS_DIR"/mltracker-*.whl
    cp "$BUILD_TMP"/mltracker-*.whl "$DOWNLOADS_DIR/"
    chown "$REPO_USER:$REPO_USER" "$DOWNLOADS_DIR"/mltracker-*.whl
    WHL_NAME=$(ls "$DOWNLOADS_DIR"/mltracker-*.whl | xargs basename)
    # Stable symlink so the wget URL never changes across version bumps
    ln -sf "$WHL_NAME" "$DOWNLOADS_DIR/mltracker-latest.whl"
    info "Wheel published: frontend/downloads/$WHL_NAME (symlinked as mltracker-latest.whl)"
else
    warn "Wheel build failed — existing wheel (if any) unchanged."
fi
rm -rf "$BUILD_TMP"

# ── Reload systemd in case the service file changed ──────────────────────────
systemctl daemon-reload

# ── Reload Nginx config ───────────────────────────────────────────────────────
info "Reloading nginx..."
if nginx -t 2>/dev/null; then
    systemctl reload nginx
else
    warn "nginx config test failed — skipping reload. Run 'nginx -t' to debug."
fi

# ── Restart MLTracker ─────────────────────────────────────────────────────────
info "Restarting mltracker..."
systemctl restart mltracker

# Wait a moment for the process to settle
sleep 2

if systemctl is-active --quiet mltracker; then
    info "mltracker is running."
else
    error "mltracker failed to start. Recent logs:"
fi

# ── Show recent logs ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}--- Recent service logs ---${NC}"
journalctl -u mltracker -n 20 --no-pager

# ── Print first 10 accounts from DB ───────────────────────────────────────────
source /etc/mltracker.env 2>/dev/null || true
DB_FILE="${DB_PATH:-$REPO_DIR/data/mltracker.db}"
if [[ -f "$DB_FILE" ]]; then
    echo ""
    echo -e "${GREEN}--- Accounts (first 10) ---${NC}"
    sqlite3 -column -header "$DB_FILE" \
        "SELECT id, status, CASE WHEN google_id IS NOT NULL THEN 'google' ELSE 'password' END AS auth, email, name FROM users ORDER BY id LIMIT 10;"
else
    warn "Database not found at $DB_FILE — skipping account list."
fi

echo ""
echo -e "${GREEN}Update complete.${NC}"
echo "  Health check: curl http://localhost:8000/health"
if [[ -n "$(ls "$REPO_DIR/frontend/downloads"/mltracker-*.whl 2>/dev/null)" ]]; then
    WHL=$(ls "$REPO_DIR/frontend/downloads"/mltracker-*.whl | xargs basename)
    echo "  SDK download: /downloads/$WHL"
fi
echo ""
