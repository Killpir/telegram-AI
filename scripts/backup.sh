#!/bin/sh
set -eu
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
db_file="backups/postgres-${stamp}.dump"
media_file="backups/broadcast-media-${stamp}.tar.gz"

echo "Creating PostgreSQL custom-format dump: ${db_file}"
docker compose exec -T postgres sh -lc 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$db_file"

echo "Creating broadcast media archive: ${media_file}"
docker compose run --rm --no-deps -T api sh -lc 'tar -C /data/broadcasts -czf - .' > "$media_file"

echo "Backup complete"
