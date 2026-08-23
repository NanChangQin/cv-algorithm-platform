#!/usr/bin/env sh
set -eu

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
backup_root=${CV_PLATFORM_BACKUP_ROOT:-"$project_root/backups"}
case "$backup_root" in
  /|"$project_root")
    echo "Refusing unsafe backup root: $backup_root" >&2
    exit 2
    ;;
esac

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="$backup_root/$timestamp"
mkdir -p "$destination"

compose="docker compose -f $project_root/compose.yaml -f $project_root/compose.database.yaml --profile database"

echo "Backing up PostgreSQL..."
$compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$destination/postgres.dump"

backup_directory() {
  source_path=$1
  archive_name=$2
  if [ ! -d "$source_path" ]; then
    echo "Skipping missing directory: $source_path"
    return
  fi
  docker run --rm \
    -v "$source_path:/source:ro" \
    -v "$destination:/backup" \
    postgres:17-alpine \
    tar -C /source -czf "/backup/$archive_name" .
}

backup_directory "$data_root" storage.tar.gz
backup_directory "$package_root" packages.tar.gz
backup_directory "$model_root" models.tar.gz

(
  cd "$destination"
  find . -maxdepth 1 -type f \( -name '*.dump' -o -name '*.tar.gz' \) -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS
)

cat > "$destination/manifest.txt" <<EOF
created_at=$timestamp
project_root=$project_root
data_root=$data_root
package_root=$package_root
model_root=$model_root
EOF

echo "Backup completed: $destination"
