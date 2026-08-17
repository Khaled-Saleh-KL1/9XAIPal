#!/usr/bin/env bash
# Prepare an Oracle Always-Free AMD micro VM (1 OCPU / ~954 MB) to run the
# 9XAIPal stack: swap, firewall, Docker. Idempotent — safe to re-run.
set -euo pipefail

SWAP_GB="${SWAP_GB:-6}"

echo "==> [1/3] swap (${SWAP_GB} GB)"
# Without swap the box cannot hold Postgres + API + Celery + SearXNG at once,
# and `docker build` alone can exhaust ~950 MB. Disk is 96 GB, so this is free.
if ! swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l "${SWAP_GB}G" /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=$((SWAP_GB*1024))
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "    swap added"
else
  echo "    swap already present"
fi
# Prefer RAM, but page out idle services rather than OOM-killing them.
sudo sysctl -qw vm.swappiness=60
sudo sysctl -qw vm.vfs_cache_pressure=50
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=60' | sudo tee -a /etc/sysctl.conf >/dev/null

echo "==> [2/3] firewall (80/443)"
# Oracle's Ubuntu images reject everything except port 22. Caddy's ACME
# HTTP-01 challenge needs 80 reachable or no certificate is ever issued.
for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true
echo "    ports 80/443 open"

echo "==> [3/3] Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh >/dev/null
  sudo usermod -aG docker "$USER" || true
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true
echo "    $(sudo docker --version)"

echo ""
free -h | head -2
swapon --show
