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
echo ""
echo -e "${GREEN}Update complete.${NC}"
echo "  Health check: curl http://localhost:8000/health"
echo ""
