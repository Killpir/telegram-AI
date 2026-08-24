#!/bin/sh
set -eu
if [ "${RESTORE_CONFIRM:-}" != "YES" ]; then
  echo "Refusing destructive restore. Re-run with RESTORE_CONFIRM=YES" >&2
  exit 2
fi
if [ "$#" -lt 1 ]; then
  echo "Usage: RESTORE_CONFIRM=YES $0 backups/postgres-....dump [broadcast-media.tar.gz]" >&2
  exit 2
fi
case "$1" in
  /*) db_file="$1" ;;
  *) db_file="$(pwd)/$1" ;;
esac
[ -f "$db_file" ] || { echo "Backup not found: $db_file" >&2; exit 2; }

echo "Stopping application services before destructive restore"
docker compose stop nginx api bot worker beat 2>/dev/null || true

echo "Restoring PostgreSQL from $db_file"
docker compose exec -T postgres sh -lc 'dropdb --force --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T postgres sh -lc 'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' < "$db_file"

if [ "${2:-}" ]; then
  case "$2" in
    /*) media_file="$2" ;;
    *) media_file="$(pwd)/$2" ;;
  esac
  [ -f "$media_file" ] || { echo "Media backup not found: $media_file" >&2; exit 2; }
  docker compose run --rm --no-deps -T api sh -lc \
    'rm -rf /data/broadcasts/* && tar -C /data/broadcasts -xzf -' < "$media_file"
fi

echo "Restore complete; application services remain stopped."
echo "Run migrations, then start the production stack, preflight and smoke checks before reopening traffic."
