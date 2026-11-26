#!/usr/bin/env bash
# Install the local Docker/GPU runtime without boot-time startup.
# Sources:
# https://docs.docker.com/engine/install/ubuntu/
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run: sudo bash scripts/install-local-runtime.sh "$USER"\n' >&2
  exit 1
fi
operator=${1:-${SUDO_USER:-}}
if [[ -z "$operator" || "$operator" == root ]] || ! id "$operator" >/dev/null 2>&1; then
  printf 'Specify the non-root local user who will manage Docker.\n' >&2
  exit 1
fi
source /etc/os-release
if [[ "$ID" != ubuntu ]]; then
  printf 'This installer supports Ubuntu only.\n' >&2
  exit 1
fi
command -v nvidia-smi >/dev/null
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

# Mask before package installation so package scripts cannot start/enable Docker.
# On failure these units stay masked; rerun this script after fixing the cause.
systemctl mask docker.service docker.socket containerd.service
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg
install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
curl -fsSL --retry 3 https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /etc/apt/keyrings/nvidia-container-toolkit.gpg
curl -fsSL --retry 3 \
  https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y --no-install-recommends \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit

if [[ -f /etc/docker/daemon.json ]]; then
  backup_dir="/var/backups/cv-platform/$(date +%Y%m%d-%H%M%S)"
  install -d -m 0700 "$backup_dir"
  cp /etc/docker/daemon.json "$backup_dir/daemon.json"
fi
nvidia-ctk runtime configure --runtime=docker
dockerd --validate --config-file=/etc/docker/daemon.json

# The docker group lets this local user manage containers (root-equivalent access).
usermod -aG docker "$operator"
install -d -m 0755 /srv/cv-platform
install -d -m 0755 -o "$operator" -g "$(id -gn "$operator")" \
  /srv/cv-platform/data /srv/cv-platform/models /srv/cv-platform/packages
systemctl unmask docker.service docker.socket containerd.service
systemctl disable docker.service docker.socket containerd.service
systemctl start docker.service
docker version
docker compose version
for unit in docker.service docker.socket containerd.service; do
  state=$(systemctl is-enabled "$unit" 2>/dev/null || true)
  printf '%s: %s\n' "$unit" "$state"
  [[ "$state" == disabled ]] || exit 1
done
printf '\nRuntime installed. Boot-time startup is disabled.\n'
printf 'Docker is running for this deployment only.\n'
