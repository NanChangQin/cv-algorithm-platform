#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ] || [ "$2" != "--confirm-restore" ]; then
  echo "Usage: $0 <backup-directory> --confirm-restore" >&2
  exit 2
fi

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_value() {
  [ -f "$project_root/.env" ] || return 0
  sed -n "s/^$1=//p" "$project_root/.env" | tail -n 1 | tr -d '\r' | sed 's/^['"'"'\"]//; s/['"'"'\"]$//'
}
data_root=${CV_PLATFORM_DATA_ROOT:-$(env_value CV_PLATFORM_DATA_ROOT)}
package_root=${CV_PLATFORM_PACKAGE_ROOT:-$(env_value CV_PLATFORM_PACKAGE_ROOT)}
model_root=${CV_PLATFORM_MODEL_ROOT:-$(env_value CV_PLATFORM_MODEL_ROOT)}
data_root=${data_root:-/srv/cv-platform/data}
package_root=${package_root:-/srv/cv-platform/packages}
model_root=${model_root:-/srv/cv-platform/models}
backup_dir=$(CDPATH= cd -- "$1" && pwd)
if [ ! -f "$backup_dir/postgres.dump" ] || [ ! -f "$backup_dir/SHA256SUMS" ]; then
  echo "Backup is incomplete: $backup_dir" >&2
  exit 2
fi

(cd "$backup_dir" && sha256sum -c SHA256SUMS)

validate_target() {
  target=$1
  case "$target" in
    /|"$project_root"|"$project_root"/)
      echo "Refusing unsafe restore target: $target" >&2
      exit 2
      ;;
  esac
}

restore_directory() {
  archive=$1
  target=$2
  [ -f "$backup_dir/$archive" ] || return
  validate_target "$target"
  parent=$(dirname -- "$target")
  name=$(basename -- "$target")
  mkdir -p "$parent"
  if [ -e "$target" ]; then
    mv -- "$target" "$target.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  mkdir -p "$target"
  docker run --rm \
    -v "$target:/target" \
    -v "$backup_dir:/backup:ro" \
    postgres:17-alpine \
    tar -C /target -xzf "/backup/$archive"
  echo "Restored $name"
}

compose="docker compose -f $project_root/compose.yaml -f $project_root/compose.database.yaml --profile database"
$compose stop frontend backend worker media-worker algorithm-manager

cat "$backup_dir/postgres.dump" | $compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner'

restore_directory storage.tar.gz "$data_root"
restore_directory packages.tar.gz "$package_root"
restore_directory models.tar.gz "$model_root"

$compose up -d
echo "Restore completed. Previous file directories were retained with a .pre-restore timestamp."
