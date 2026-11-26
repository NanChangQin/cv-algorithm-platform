#!/usr/bin/env bash
# Install all five locally downloaded official YOLO26 detection weights.
# Backend and shared YOLO inference images must already be built and running.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"
weights_dir=${1:-runtimes/yolo-trainer/assets}
for scale in n s m l x; do
  test -s "$weights_dir/yolo26$scale.pt" || { printf 'Missing weight: yolo26%s.pt\n' "$scale" >&2; exit 1; }
done
compose=(docker compose -f compose.yaml -f compose.database.yaml -f compose.local.yaml --profile database)
staging=$("${compose[@]}" exec -T backend mktemp -d /tmp/yolo26-install.XXXXXXXX)
trap '"${compose[@]}" exec -T backend rm -rf -- "$staging" >/dev/null' EXIT
for scale in n s m l x; do
  "${compose[@]}" cp "$weights_dir/yolo26$scale.pt" "backend:$staging/yolo26$scale.pt"
done
args=(--weights "$staging")
if [[ -n ${CV_PLATFORM_TARGET_PROJECT:-} ]]; then args+=(--project "$CV_PLATFORM_TARGET_PROJECT"); fi
"${compose[@]}" exec -T backend python -m cv_platform.modules.algorithms.pretrained "${args[@]}"
