#!/usr/bin/env bash
# Set up 4 GiB compressed ZRAM swap + desktop sysctl tuning on Ubuntu.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0"
  exit 1
fi

echo "[1/4] Installing systemd-zram-generator..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y systemd-zram-generator

echo "[2/4] Configuring 4 GiB zstd ZRAM swap (/etc/systemd/zram-generator.conf)..."
cat << 'EOF' > /etc/systemd/zram-generator.conf
[zram0]
zram-size = min(ram / 2, 4096)
compression-algorithm = zstd
swap-priority = 100
EOF

echo "[3/4] Writing /etc/sysctl.d/99-desktop-perf.conf..."
cat << 'EOF' > /etc/sysctl.d/99-desktop-perf.conf
# Prefer keeping app memory in RAM and avoid thrashing the 5400 RPM HDD swapfile
vm.swappiness = 15
vm.vfs_cache_pressure = 50
vm.page-cluster = 0
EOF

echo "[4/4] Activating ZRAM and applying sysctl..."
systemctl daemon-reload
systemctl start systemd-zram-setup@zram0.service
sysctl --system >/dev/null

echo
echo "=== Verification ==="
swapon --show
sysctl vm.swappiness vm.vfs_cache_pressure vm.page-cluster
