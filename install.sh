#!/bin/bash
set -e

# OpenClaw One-Click Installation Script
# Usage: bash install.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 1. Check OS ──────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    log_error "This script only supports Linux."
    exit 1
fi

# ── 2. Install Docker if missing ─────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log_info "Docker not found. Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    log_info "Docker installed successfully."
else
    log_info "Docker $(docker --version) is already installed."
fi

# ── 3. Install Docker Compose (plugin or standalone) ─────────────────────────
if ! docker compose version &>/dev/null 2>&1; then
    log_info "Installing Docker Compose plugin..."
    DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}"
    mkdir -p "$DOCKER_CONFIG/cli-plugins"
    COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest \
        | grep '"tag_name"' | head -1 | cut -d'"' -f4)
    curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
        -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
    chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
    log_info "Docker Compose ${COMPOSE_VERSION} installed."
else
    log_info "Docker Compose $(docker compose version --short) is already installed."
fi

# ── 4. Set up configuration ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        log_warn ".env file created from .env.example."
        log_warn "Please edit .env and fill in your configuration, then re-run this script."
        log_warn "  nano .env   (or your preferred editor)"
        exit 0
    else
        log_error ".env.example not found. Cannot create configuration file."
        exit 1
    fi
fi

# ── 5. Validate required environment variables ───────────────────────────────
source .env

MISSING=()
[[ -z "${TELEGRAM_BOT_TOKEN}" || "${TELEGRAM_BOT_TOKEN}" == "your_telegram_bot_token_here" ]] \
    && MISSING+=("TELEGRAM_BOT_TOKEN")
[[ -z "${FEISHU_WEBHOOK_URL}" || "${FEISHU_WEBHOOK_URL}" == *"your_webhook_key_here"* ]] \
    && MISSING+=("FEISHU_WEBHOOK_URL")

if [[ ${#MISSING[@]} -gt 0 ]]; then
    log_error "The following required variables are not configured in .env:"
    for v in "${MISSING[@]}"; do
        log_error "  - $v"
    done
    log_warn "Please edit .env and set the values, then re-run this script."
    exit 1
fi

# ── 6. Create required directories ───────────────────────────────────────────
mkdir -p logs
log_info "Log directory ready."

# ── 7. Build and start services ──────────────────────────────────────────────
log_info "Building Docker image..."
docker compose build

log_info "Starting OpenClaw services..."
docker compose up -d

# ── 8. Verify ────────────────────────────────────────────────────────────────
log_info "Waiting for service to become healthy..."
RETRIES=10
HEALTHY=false
while [[ $RETRIES -gt 0 ]]; do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' openclaw 2>/dev/null || true)
    if [[ "$STATUS" == "healthy" ]]; then
        HEALTHY=true
        break
    fi
    sleep 3
    RETRIES=$((RETRIES - 1))
done

if [[ "$HEALTHY" == "true" ]]; then
    log_info "✅ OpenClaw is running and healthy!"
else
    log_warn "Service started but health check is still pending."
    log_warn "Run 'docker compose logs -f' to monitor the output."
fi

echo ""
log_info "Useful commands:"
echo "  View logs:    docker compose logs -f"
echo "  Stop:         docker compose down"
echo "  Restart:      docker compose restart"
echo "  Update:       git pull && bash install.sh"
