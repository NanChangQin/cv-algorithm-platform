#!/usr/bin/env bash
# Ubuntu/Debian execution-node setup. Keeps the installed NVIDIA driver intact.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  printf 'Run with sudo bash scripts/install-node-gpu.sh\n' >&2
  exit 1
fi
command -v nvidia-smi >/dev/null
command -v docker >/dev/null
command -v curl >/dev/null
command -v gpg >/dev/null

backup_dir="/var/backups/cv-platform-node/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup_dir"
for config in /etc/docker/daemon.json /etc/nvidia-container-runtime/config.toml; do
  if [ -f "$config" ]; then cp --parents "$config" "$backup_dir/"; fi
done
if ! command -v nvidia-ctk >/dev/null; then
  curl --fail --silent --show-error --location --retry 3 \
    https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl --fail --silent --show-error --location --retry 3 \
    https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -o Dir::Etc::sourcelist=sources.list.d/nvidia-container-toolkit.list \
    -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nvidia-container-toolkit
fi
nvidia-ctk runtime configure --runtime=docker
dockerd --validate --config-file=/etc/docker/daemon.json
# Docker supports reloading its runtimes list without restarting running services.
systemctl reload docker
docker info --format '{{range $name, $runtime := .Runtimes}}{{$name}} {{end}}'
printf 'Configuration backup: %s\n' "$backup_dir"
