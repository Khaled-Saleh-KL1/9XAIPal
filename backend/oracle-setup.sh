#!/usr/bin/env bash
#
# oracle-setup.sh — bootstrap a fresh Oracle Cloud "Always Free" ARM VM to run
# the 9XAIPal backend (API + Celery worker + Postgres + Redis + SearXNG).
#
# Run it ONCE on the VM, from the repo's backend/ folder:
#   ./oracle-setup.sh
#
# It will:
#   1. Install Docker + the Compose plugin (idempotent).
#   2. Create .env from .env.oracle if it doesn't exist.
#   3. Build and start the stack (docker-compose.oracle.yml).
#   4. Print the public URL to paste into Vercel as VITE_API_BASE_URL.
#
# You must first fill in OLLAMA_API_KEY and POSTGRES_PASSWORD in .env.oracle
# (or in .env after step 2) — the compose file refuses to start without them.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> [1/4] Installing Docker + Compose plugin (idempotent)..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" 2>/dev/null || true
  echo "    Docker installed. You may need to re-login (newgrp docker) for the group."
else
  echo "    Docker already present: $(docker --version)"
fi
# Compose v2 plugin (compose v1 is EOL).
if ! docker compose version >/dev/null 2>&1; then
  echo "    Installing Docker Compose plugin..."
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
docker compose version

echo "==> [2/4] Ensuring .env exists..."
if [[ ! -f .env ]]; then
  if [[ -f .env.oracle ]]; then
    cp .env.oracle .env
    echo "    Created .env from .env.oracle — EDIT IT and set OLLAMA_API_KEY + POSTGRES_PASSWORD."
    echo "    Then re-run this script."
    exit 0
  else
    echo "    ERROR: no .env and no .env.oracle to copy from." >&2
    exit 1
  fi
fi
# Refuse to start with the placeholder password.
if grep -q '^POSTGRES_PASSWORD=change-me' .env 2>/dev/null; then
  echo "    ERROR: POSTGRES_PASSWORD is still the placeholder. Edit .env and re-run." >&2
  exit 1
fi
if grep -q '^OLLAMA_API_KEY=$' .env 2>/dev/null; then
  echo "    ERROR: OLLAMA_API_KEY is empty. Edit .env and re-run." >&2
  exit 1
fi
# Embeddings run on Gemini — the Ollama key covers chat/vision only.
if grep -q '^GEMINI_API_KEY=$' .env 2>/dev/null; then
  echo "    ERROR: GEMINI_API_KEY is empty (embeddings need it). Edit .env and re-run." >&2
  exit 1
fi
# Caddy needs the public hostname to request a certificate for.
if grep -q '^CADDY_SITE_ADDRESS=$' .env 2>/dev/null; then
  IP_GUESS="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  echo "    ERROR: CADDY_SITE_ADDRESS is empty." >&2
  [[ -n "$IP_GUESS" ]] && echo "    For this VM use: CADDY_SITE_ADDRESS=${IP_GUESS//./-}.sslip.io" >&2
  exit 1
fi

# Oracle's Ubuntu images reject everything except port 22 by default; Caddy's
# ACME HTTP-01 challenge needs 80 reachable or certificate issuance fails.
echo "==> opening ports 80/443 in the VM firewall..."
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "==> [3/4] Building and starting the stack..."
docker compose -f docker-compose.oracle.yml up -d --build

echo "==> [4/4] Waiting for the API healthcheck..."
for i in $(seq 1 30); do
  if curl -fsS "http://localhost:8000/api/v1/health" >/dev/null 2>&1; then
    echo "    API is healthy."
    break
  fi
  echo "    ... ($i/30) not ready yet"
  sleep 5
done

# The public URL is the Caddy site address (HTTPS, Let's Encrypt).
SITE="$(grep -E '^CADDY_SITE_ADDRESS=' .env | cut -d= -f2-)"
echo ""
echo "============================================================"
echo " 9XAIPal backend is running on this VM."
echo " API health (local):  http://localhost:8000/api/v1/health"
echo " Public URL (HTTPS):  https://${SITE}/api/v1/health"
echo ""
echo " Caddy fetches a Let's Encrypt certificate on first start; give it"
echo " up to a minute, then check:  curl https://${SITE}/api/v1/health"
echo " If it fails:  docker compose -f docker-compose.oracle.yml logs caddy"
echo ""
echo " Next: in Vercel (project 9xaipal) set the build-time env var"
echo "   VITE_API_BASE_URL=https://${SITE}"
echo " and redeploy — it is baked in at build time, so a redeploy is required."
echo "============================================================"
