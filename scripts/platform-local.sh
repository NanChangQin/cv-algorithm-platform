#!/usr/bin/env bash
# Always include the no-restart override for this local deployment.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"
command -v docker >/dev/null || { printf 'Install Docker first.\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  printf 'Start Docker with: sudo systemctl start docker\n' >&2
  printf 'For new docker group membership, log in again or run this script with sg docker.\n' >&2
  exit 1
}
compose=(docker compose -f compose.yaml -f compose.database.yaml -f compose.local.yaml --profile database)
build_images() {
  # Host networking lets build steps reach the loopback-only local proxy.
  # Bake requires explicit permission in addition to Compose's entitlement.
  "${compose[@]}" --profile algorithm-images build --print "$@" \
    | docker buildx bake --file - --allow=network.host --load
}
action=${1:-status}
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  # Build the shared backend image before Compose creates the image-only worker.
  up)
    needs_build=false
    for service in backend algorithm-manager media-worker frontend database-migrator; do
      docker image inspect "cv-platform/$service:dev" >/dev/null 2>&1 || needs_build=true
    done
    if "$needs_build"; then
      build_images backend algorithm-manager media-worker frontend database-migrator
    fi
    "${compose[@]}" up -d --no-build --pull missing "$@"
    ;;
  build) build_images backend algorithm-manager media-worker frontend database-migrator "$@" ;;
  down) "${compose[@]}" down "$@" ;;
  stop) "${compose[@]}" stop "$@" ;;
  status) "${compose[@]}" ps -a "$@" ;;
  logs) "${compose[@]}" logs --tail 100 "$@" ;;
  build-models) build_images yolo yolo-trainer "$@" ;;
  config-check) "${compose[@]}" config --quiet "$@" ;;
  *) printf 'Usage: %s {up|down|stop|status|logs|build|build-models|config-check}\n' "$0" >&2; exit 2 ;;
esac
