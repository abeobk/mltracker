#!/usr/bin/env bash
# =============================================================================
# update.sh — Deploy code updates to a running MLTracker server
#
# Usage:
#   sudo bash ~/mltracker/setup/update.sh
#
# What it does:
#   - Pulls latest code from git
#   - Installs any new Python dependencies
#   - Restarts the MLTracker service
#   - Prints recent logs to confirm healthy start
# =============================================================================

set -euo pipefail

REPO_DIR="/home/ubuntu/mltracker"
VENV_DIR="$REPO_DIR/backend/venv"

GREEN='\\033[0;32m'; YELLOW='\\033[1;33m'; RED='\\033[0;31m'; NC='\\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0"
[[ ! -d "$REPO_DIR" ]] && error "Repo not found at $REPO_DIR"

# ── Pull latest code ──────────────────────────────────────────────────────────
info "Pulling latest code..."
sudo -u ubuntu git -C "$REPO_DIR" pull --ff-only

# ── Update Python dependencies ────────────────────────────────────────────────
info "Updating Python dependencies..."
sudo -u ubuntu "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u ubuntu "$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/backend/requirements.txt"

# ── Reload systemd in case the service file changed ──────────────────────────
systemctl daemon-reload

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
