#!/usr/bin/env bash
# Use this machine's existing local HTTP proxy for registry downloads.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  printf 'Run with sudo bash scripts/configure-local-docker-proxy.sh\n' >&2
  exit 1
fi
install -d -m 0755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/cv-platform-proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7897"
Environment="HTTPS_PROXY=http://127.0.0.1:7897"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
EOF
systemctl daemon-reload
systemctl disable docker.service docker.socket containerd.service
systemctl restart docker.service
printf 'Docker now uses the local proxy on port 7897; boot-time startup remains disabled.\n'
