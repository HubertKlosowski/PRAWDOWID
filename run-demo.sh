#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

pg_root=/tmp/prawdowid-pg
pg_data="$pg_root/db"
export PGHOST="$pg_root"
export PGPORT=5432
export PGDATABASE=prawdowid

if [[ ! -f "$pg_data/PG_VERSION" ]]; then
    echo "Brak istniejącej bazy PostgreSQL w $pg_data." >&2
    echo "Zrzut bazy jest dostępny w data/prawdowid-demo-2026-11-21.dump." >&2
    exit 1
fi

if ! pg_isready -q; then
    pg_version=$(cat "$pg_data/PG_VERSION")
    "/usr/lib/postgresql/$pg_version/bin/pg_ctl" \
        -D "$pg_data" -l "$pg_root/server.log" \
        -o "-k $pg_root -p $PGPORT -c listen_addresses=" -w start
fi

psql -X -v ON_ERROR_STOP=1 -c 'SELECT count(*) AS fragmenty FROM verify_chunk;'
uv sync
uv run python manage.py migrate
env_file=()
if [[ -f .env ]]; then
    env_file=(--env-file .env)
fi
exec uv run "${env_file[@]}" python manage.py serve_demo --noreload 127.0.0.1:8000
