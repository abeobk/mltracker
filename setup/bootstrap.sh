#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — One-time server setup for MLTracker
#                Supports: Ubuntu 20.04/22.04, Amazon Linux 2, Amazon Linux 2023
#
# Usage:
#   1. SSH into your EC2 instance
#   2. Clone the repo anywhere: git clone <your-repo-url> ~/mltracker
#   3. Run from any location:   sudo bash ~/mltracker/setup/bootstrap.sh
#
# What it does:
#   - Detects the Linux distro and uses the correct package manager
#   - Installs system packages (Python 3.11, Nginx, Redis, Certbot, Git)
#   - Mounts the data EBS volume to /mnt/mltracker_data
#   - Creates Python venv and installs pip dependencies
#   - Deploys the secrets template, systemd service, and Nginx config
#   - Starts Redis, Gunicorn, and Nginx
#
# After this script completes:
#   1. Fill in /etc/mltracker.env with real secrets
#   2. sudo systemctl restart mltracker
#   3. Once DNS is pointing at this server, run: sudo bash setup/certbot.sh
# =============================================================================

set -euo pipefail

# Resolve repo root from the script's own location (works wherever it is cloned)
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$REPO_DIR/backend/venv"
DATA_MOUNT="/mnt/mltracker_data"
SERVICE_NAME="mltracker"
LOG_DIR="/var/log/gunicorn"
# Detect the user who owns the repo (works for ubuntu, ec2-user, or any other login)
REPO_USER="$(stat -c '%U' "$REPO_DIR")"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0"
info "Using repo at $REPO_DIR (owner: $REPO_USER)"

# =============================================================================
# 0. Distro detection
# =============================================================================
DISTRO_ID=""
DISTRO_VERSION=""
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    DISTRO_ID="${ID:-}"
    DISTRO_VERSION="${VERSION_ID:-}"
fi

# Normalise Amazon Linux variant
if [[ "$DISTRO_ID" == "amzn" && "$DISTRO_VERSION" == "2023" ]]; then
    DISTRO="al2023"
elif [[ "$DISTRO_ID" == "amzn" ]]; then
    DISTRO="al2"
elif [[ "$DISTRO_ID" == "ubuntu" || "$DISTRO_ID" == "debian" ]]; then
    DISTRO="ubuntu"
else
    warn "Unrecognised distro '$DISTRO_ID' — assuming Ubuntu/Debian behaviour."
    DISTRO="ubuntu"
fi
info "Detected distro: $DISTRO_ID $DISTRO_VERSION (profile: $DISTRO)"

# Per-distro variables
case "$DISTRO" in
  ubuntu)
    PYTHON_BIN="python3.11"
    REDIS_SERVICE="redis-server"
    # Ubuntu uses sites-available / sites-enabled pattern
    NGINX_CONF_DIR="/etc/nginx/sites-available"
    NGINX_ENABLED_DIR="/etc/nginx/sites-enabled"
    NGINX_SITE="$NGINX_CONF_DIR/$SERVICE_NAME"
    USE_SITES_ENABLED=true
    ;;
  al2023)
    PYTHON_BIN="python3.11"
    REDIS_SERVICE="redis6"
    # Amazon Linux uses conf.d drop-in files
    NGINX_CONF_DIR="/etc/nginx/conf.d"
    NGINX_SITE="$NGINX_CONF_DIR/$SERVICE_NAME.conf"
    USE_SITES_ENABLED=false
    ;;
  al2)
    PYTHON_BIN="python3"   # AL2 ships Python 3.7; 3.11 installed below via extras
    REDIS_SERVICE="redis"
    NGINX_CONF_DIR="/etc/nginx/conf.d"
    NGINX_SITE="$NGINX_CONF_DIR/$SERVICE_NAME.conf"
    USE_SITES_ENABLED=false
    ;;
esac

# =============================================================================
# 1. System packages
# =============================================================================
info "Installing system packages..."

case "$DISTRO" in
  ubuntu)
    apt-get update -qq
    apt-get install -y -qq \
        python3.11 python3.11-venv python3-pip \
        nginx redis-server \
        certbot python3-certbot-nginx \
        git curl jq logrotate
    ;;
  al2023)
    dnf update -q -y
    # AL2023 ships curl-minimal which conflicts with the full curl package.
    # curl-minimal is sufficient; skip curl to avoid the conflict.
    dnf install -y -q \
        python3.11 python3.11-pip \
        nginx redis6 \
        certbot python3-certbot-nginx \
        jq logrotate
    # git is pre-installed on AL2023 AMIs but install to be safe
    dnf install -y -q git || true
    # Make python3.11 available as the venv binary
    alternatives --install /usr/bin/python3.11 python3.11 "$(command -v python3.11)" 10 || true
    ;;
  al2)
    yum update -q -y
    # Enable extras for Python 3.8 (closest available; 3.11 not in AL2 repos)
    amazon-linux-extras install -y python3.8 nginx1 epel || true
    yum install -y -q \
        python38 python38-pip \
        nginx redis \
        certbot python3-certbot-nginx \
        git jq logrotate
    # Point python3.11 variable to available python3
    PYTHON_BIN="python3.8"
    warn "Amazon Linux 2 does not ship Python 3.11 — using Python 3.8. Consider upgrading to AL2023."
    ;;
esac

# =============================================================================
# 2. EBS data volume
# =============================================================================
info "Setting up data volume at $DATA_MOUNT..."

# Find the secondary EBS volume (not the root device).
# Adjust DEVICE if your instance uses a different device name (e.g. /dev/nvme1n1).
DEVICE=""
for dev in /dev/xvdf /dev/nvme1n1 /dev/sdb; do
    if [[ -b "$dev" ]]; then
        DEVICE="$dev"
        break
    fi
done

if [[ -z "$DEVICE" ]]; then
    warn "No secondary block device found. Using root volume for data (not recommended for production)."
    DATA_MOUNT="/home/$REPO_USER/mltracker_data"
    mkdir -p "$DATA_MOUNT"
else
    # Format only if the device has no filesystem yet
    if ! blkid "$DEVICE" &>/dev/null; then
        info "Formatting $DEVICE as ext4..."
        mkfs.ext4 -q "$DEVICE"
    fi

    mkdir -p "$DATA_MOUNT"
    mount "$DEVICE" "$DATA_MOUNT" 2>/dev/null || true

    # Persist in fstab if not already there
    if ! grep -q "$DEVICE" /etc/fstab; then
        UUID=$(blkid -s UUID -o value "$DEVICE")
        echo "UUID=$UUID  $DATA_MOUNT  ext4  defaults,nofail  0  2" >> /etc/fstab
        info "Added $DEVICE to /etc/fstab (UUID=$UUID)"
    fi
fi

mkdir -p "$DATA_MOUNT/mltracker"
mkdir -p "$DATA_MOUNT/backups"
chown -R "$REPO_USER:$REPO_USER" "$DATA_MOUNT"
info "Data volume ready at $DATA_MOUNT"

# =============================================================================
# 3. Python venv + dependencies
# =============================================================================
info "Creating Python venv..."
sudo -u "$REPO_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
sudo -u "$REPO_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$REPO_USER" "$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/backend/requirements.txt"
info "Python dependencies installed."

# =============================================================================
# 4. Secrets file
# =============================================================================
if [[ ! -f /etc/mltracker.env ]]; then
    cp "$REPO_DIR/setup/env.template" /etc/mltracker.env
    chmod 600 /etc/mltracker.env
    chown root:root /etc/mltracker.env
    warn "Secrets file created at /etc/mltracker.env — FILL IN ALL VALUES before starting the service!"
else
    info "/etc/mltracker.env already exists — skipping."
fi

# =============================================================================
# 5. Gunicorn log directory
# =============================================================================
mkdir -p "$LOG_DIR"
chown "$REPO_USER:$REPO_USER" "$LOG_DIR"

# =============================================================================
# 6. Logrotate config for Gunicorn
# =============================================================================
cat > /etc/logrotate.d/gunicorn <<'EOF'
/var/log/gunicorn/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    copytruncate
}
EOF
info "Logrotate configured for Gunicorn."

# =============================================================================
# 7. Systemd service
# =============================================================================
# Install service file — substitute placeholders with detected values
sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
    -e "s|__REPO_USER__|$REPO_USER|g" \
    -e "s|__REDIS_SERVICE__|$REDIS_SERVICE|g" \
    "$REPO_DIR/setup/mltracker.service" > /etc/systemd/system/mltracker.service
systemctl daemon-reload
systemctl enable mltracker
info "Systemd service installed and enabled."

# =============================================================================
# 8. Nginx config
# =============================================================================
# Prompt for the domain name
read -rp "Enter your domain name (e.g. mltracker.example.com): " DOMAIN
[[ -z "$DOMAIN" ]] && error "Domain name cannot be empty."

cat > "$NGINX_SITE" <<EOF
# MLTracker — Nginx site config
# Generated by bootstrap.sh on $(date)

server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 20m;

    # Serve Vue SPA static files directly
    root $REPO_DIR/frontend;
    index index.html;

    # API, auth, files, health → Gunicorn
    location ~ ^/(api|auth|files|health)/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }

    # SPA fallback
    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

# Enable site (Ubuntu: symlink into sites-enabled; Amazon Linux: conf.d file is live directly)
if [[ "$USE_SITES_ENABLED" == true ]]; then
    ln -sf "$NGINX_SITE" "$NGINX_ENABLED_DIR/$SERVICE_NAME"
    rm -f "$NGINX_ENABLED_DIR/default"
fi

nginx -t && info "Nginx config valid." || error "Nginx config has errors — check $NGINX_SITE"

# =============================================================================
# 9. Redis — start and enable
# =============================================================================
systemctl enable "$REDIS_SERVICE"
systemctl start "$REDIS_SERVICE"
info "Redis started ($REDIS_SERVICE)."

# =============================================================================
# 10. Start services
# =============================================================================
info "Starting Nginx..."
systemctl enable nginx
systemctl restart nginx

# Don't start mltracker until secrets are filled in
warn "Gunicorn service NOT started yet — fill in /etc/mltracker.env first, then run:"
warn "  sudo systemctl start mltracker"
warn "  sudo systemctl status mltracker"

# =============================================================================
# Done
# =============================================================================
echo ""
echo -e "${GREEN}=======================================================${NC}"
echo -e "${GREEN} Bootstrap complete!${NC}"
echo -e "${GREEN}=======================================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit secrets:    sudo nano /etc/mltracker.env"
echo "  2. Start app:       sudo systemctl start mltracker"
echo "  3. Check health:    curl http://localhost:8000/health"
echo "  4. After DNS ready: sudo bash $REPO_DIR/setup/certbot.sh"
echo ""
