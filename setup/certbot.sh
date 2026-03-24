#!/usr/bin/env bash
# =============================================================================
# certbot.sh — Obtain / renew TLS certificate and enable HTTPS
#
# Usage:
#   Run AFTER bootstrap.sh and AFTER your DNS A-record is pointing at this IP.
#   sudo bash ~/mltracker/setup/certbot.sh
#
# What it does:
#   - Obtains a Let's Encrypt certificate via certbot --nginx
#   - Sets SESSION_COOKIE_SECURE=true in /etc/mltracker.env
#   - Restarts MLTracker so the new setting takes effect
# =============================================================================

set -euo pipefail

GREEN='\\033[0;32m'; YELLOW='\\033[1;33m'; RED='\\033[0;31m'; NC='\\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0"

ENV_FILE="/etc/mltracker.env"
[[ ! -f "$ENV_FILE" ]] && error "$ENV_FILE not found — run bootstrap.sh first."

# ── Prompt for details ────────────────────────────────────────────────────────
read -rp "Domain name (must match Nginx server_name): " DOMAIN
[[ -z "$DOMAIN" ]] && error "Domain cannot be empty."

read -rp "Admin email for Let's Encrypt expiry notices: " EMAIL
[[ -z "$EMAIL" ]] && error "Email cannot be empty."

# ── Obtain certificate ────────────────────────────────────────────────────────
info "Running certbot for $DOMAIN..."
certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect \
    -d "$DOMAIN"

info "Certificate obtained. Nginx reloaded by certbot."

# ── Flip SESSION_COOKIE_SECURE ────────────────────────────────────────────────
if grep -q "^SESSION_COOKIE_SECURE=" "$ENV_FILE"; then
    sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=true/' "$ENV_FILE"
    info "SESSION_COOKIE_SECURE set to true in $ENV_FILE"
else
    echo "SESSION_COOKIE_SECURE=true" >> "$ENV_FILE"
    warn "SESSION_COOKIE_SECURE appended to $ENV_FILE"
fi

# ── Restart MLTracker ─────────────────────────────────────────────────────────
info "Restarting mltracker service..."
systemctl restart mltracker
systemctl is-active --quiet mltracker && info "mltracker is running." || error "mltracker failed to start — check: journalctl -u mltracker -n 50"

# ── Auto-renewal check ────────────────────────────────────────────────────────
info "Testing certbot auto-renewal..."
certbot renew --dry-run

echo ""
echo -e "${GREEN}=======================================================${NC}"
echo -e "${GREEN} HTTPS setup complete!${NC}"
echo -e "${GREEN}=======================================================${NC}"
echo ""
echo "  Site:    https://$DOMAIN"
echo "  Renews:  automatically via /etc/cron.d/certbot (or systemd timer)"
echo ""
