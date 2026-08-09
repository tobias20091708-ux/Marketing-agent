#!/bin/bash
# ============================================
# AI OPERATIONS PLATFORM — ONE-CLICK SETUP
# Run on a fresh Ubuntu 22.04+ VPS:
#   curl -sSL https://your-repo/setup.sh | bash
# Or: chmod +x setup.sh && ./setup.sh
# ============================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[i]${NC} $1"; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   AI OPERATIONS PLATFORM — INSTALLER     ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# --- Check root ---
if [ "$EUID" -ne 0 ]; then
    error "Please run as root: sudo ./setup.sh"
fi

# --- Check OS ---
if ! grep -q "Ubuntu\|Debian" /etc/os-release 2>/dev/null; then
    warn "This script is designed for Ubuntu/Debian. Proceeding anyway..."
fi

# --- Install Docker ---
if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    log "Docker installed"
else
    log "Docker already installed"
fi

# --- Install Docker Compose ---
if ! command -v docker compose &>/dev/null; then
    info "Installing Docker Compose plugin..."
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin
    log "Docker Compose installed"
else
    log "Docker Compose already installed"
fi

# --- Install extras ---
apt-get install -y -qq curl git ufw fail2ban > /dev/null 2>&1
log "System packages installed"

# --- Setup directory ---
INSTALL_DIR="/opt/ai-platform"
mkdir -p "$INSTALL_DIR"

# Copy files if running from the project directory
if [ -f "docker-compose.yml" ]; then
    cp -r . "$INSTALL_DIR/"
    log "Project files copied to $INSTALL_DIR"
else
    warn "No project files found. Make sure to copy them to $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- Generate secrets ---
if [ ! -f .env ]; then
    cp .env.example .env

    # Generate random passwords
    SECRET_KEY=$(openssl rand -hex 32)
    DB_PASSWORD=$(openssl rand -hex 16)
    ADMIN_PASSWORD=$(openssl rand -hex 12)

    sed -i "s/generate-with-openssl-rand-hex-32/$SECRET_KEY/" .env
    sed -i "s/change-this-password/$DB_PASSWORD/g" .env
    sed -i "s/changeme-use-strong-password/$ADMIN_PASSWORD/" .env

    log "Generated secure passwords"
    info "Admin password: $ADMIN_PASSWORD (save this!)"
else
    warn ".env already exists, skipping generation"
fi

# --- Setup domain ---
read -p "Enter your domain (or press Enter for localhost): " DOMAIN
DOMAIN=${DOMAIN:-localhost}
sed -i "s/ai.yourdomain.com/$DOMAIN/" .env
sed -i "s/\${DOMAIN:-localhost}/$DOMAIN/" Caddyfile
log "Domain set to: $DOMAIN"

# --- Enter API key ---
read -p "Enter your Anthropic API key (sk-ant-...): " API_KEY
if [ -n "$API_KEY" ]; then
    sed -i "s|ANTHROPIC_API_KEY=sk-ant-...|ANTHROPIC_API_KEY=$API_KEY|" .env
    log "API key configured"
else
    warn "No API key entered. Set ANTHROPIC_API_KEY in .env before starting."
fi

# --- Create data directories ---
mkdir -p data/files data/logs
log "Data directories created"

# --- Firewall ---
info "Configuring firewall..."
ufw --force reset > /dev/null 2>&1
ufw default deny incoming > /dev/null 2>&1
ufw default allow outgoing > /dev/null 2>&1
ufw allow ssh > /dev/null 2>&1
ufw allow 80/tcp > /dev/null 2>&1
ufw allow 443/tcp > /dev/null 2>&1
ufw --force enable > /dev/null 2>&1
log "Firewall configured (SSH, HTTP, HTTPS only)"

# --- Setup fail2ban ---
systemctl enable fail2ban > /dev/null 2>&1
systemctl start fail2ban > /dev/null 2>&1
log "Fail2ban enabled"

# --- Setup automatic updates ---
apt-get install -y -qq unattended-upgrades > /dev/null 2>&1
log "Automatic security updates enabled"

# --- Setup daily backup cron ---
cat > /etc/cron.daily/ai-platform-backup << 'BACKUP'
#!/bin/bash
BACKUP_DIR="/opt/ai-platform/backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
cd /opt/ai-platform
docker compose exec -T postgres pg_dump -U aiplatform aiplatform | gzip > "$BACKUP_DIR/db_$DATE.gz"
# Keep only last 30 days
find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete
BACKUP
chmod +x /etc/cron.daily/ai-platform-backup
log "Daily database backup configured"

# --- Build and start ---
info "Building containers (this takes 2-5 minutes)..."
docker compose build --quiet

info "Starting all services..."
docker compose up -d

# --- Wait for services ---
info "Waiting for services to start..."
sleep 10

# Check health
for i in {1..30}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    log "All services are running!"
else
    warn "Services may still be starting. Check with: docker compose logs"
fi

# --- Create systemd service for auto-start ---
cat > /etc/systemd/system/ai-platform.service << SERVICE
[Unit]
Description=AI Operations Platform
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/ai-platform
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable ai-platform
log "Auto-start on boot enabled"

# --- Done! ---
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   INSTALLATION COMPLETE!                      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""

if [ "$DOMAIN" = "localhost" ]; then
    IP=$(curl -sf https://ifconfig.me || echo "your-server-ip")
    echo -e "  Dashboard:  ${BLUE}http://$IP:8000${NC}"
else
    echo -e "  Dashboard:  ${BLUE}https://$DOMAIN${NC}"
fi
echo -e "  API:        ${BLUE}http://localhost:8000/api${NC}"
echo ""
echo -e "  ${YELLOW}Next steps:${NC}"
echo -e "  1. Edit ${BLUE}/opt/ai-platform/.env${NC} to add integration keys"
echo -e "     (Gmail, Slack, Stripe, GitHub, etc.)"
echo -e "  2. Restart: ${BLUE}cd /opt/ai-platform && docker compose restart${NC}"
echo -e "  3. View logs: ${BLUE}docker compose logs -f${NC}"
echo ""
echo -e "  ${YELLOW}Useful commands:${NC}"
echo -e "  Stop:    docker compose down"
echo -e "  Start:   docker compose up -d"
echo -e "  Logs:    docker compose logs -f app"
echo -e "  Worker:  docker compose logs -f worker"
echo -e "  DB:      docker compose exec postgres psql -U aiplatform"
echo ""
