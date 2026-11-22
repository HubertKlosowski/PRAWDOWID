#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

mode=${1:-}
if [[ $# -gt 1 || ( -n "$mode" && "$mode" != --discord && "$mode" != --prepare ) ]]; then
    echo "Użycie: $0 [--prepare | --discord]" >&2
    exit 2
fi

fail() {
    echo "$1" >&2
    exit 1
}

for command_name in uv pg_config pg_isready psql createdb pg_restore flock; do
    command -v "$command_name" >/dev/null || fail "Brakuje programu $command_name. Sprawdź wymagania uruchomienia w README."
done

pg_root=${PRAWDOWID_DEMO_PG_ROOT:-/tmp/prawdowid-pg}
mkdir -m 700 -p -- "$pg_root" || fail "Nie można utworzyć katalogu bazy demonstracyjnej."
pg_root=$(cd -- "$pg_root" && pwd -P)
[[ "$pg_root" != *"'"* && "$pg_root" != *$'\n'* ]] ||
    fail "Ścieżka katalogu PostgreSQL nie może zawierać apostrofu ani znaku nowego wiersza."
pg_data="$pg_root/db"
export PGHOST="$pg_root"
export PGPORT=5432
export PGDATABASE=prawdowid

# WWW i Discord mogą startować w osobnych terminalach.
exec 9>"$pg_root/.prepare.lock"
flock -x 9

pg_bin=$(pg_config --bindir)
if [[ -f "$pg_data/PG_VERSION" ]]; then
    pg_version=$(cat "$pg_data/PG_VERSION")
    if [[ -x "/usr/lib/postgresql/$pg_version/bin/pg_ctl" ]]; then
        pg_bin="/usr/lib/postgresql/$pg_version/bin"
    fi
else
    [[ -x "$pg_bin/initdb" ]] || fail "Brakuje initdb. Zainstaluj lokalny serwer PostgreSQL."
    "$pg_bin/initdb" -D "$pg_data" --encoding=UTF8 --locale=C.UTF-8 \
        --auth-local=peer --auth-host=scram-sha-256 ||
        fail "Nie udało się zainicjować lokalnego PostgreSQL w $pg_data."
fi

[[ -x "$pg_bin/pg_ctl" ]] || fail "Brakuje pg_ctl dla lokalnego klastra PostgreSQL."
if ! pg_isready -q; then
    "$pg_bin/pg_ctl" -D "$pg_data" -l "$pg_root/server.log" \
        -o "-k '$pg_root' -p $PGPORT -c listen_addresses=" -w start 9>&- ||
        fail "Nie udało się uruchomić PostgreSQL. Sprawdź $pg_root/server.log."
fi

if ! installed_vector=$(psql -X -d postgres -At -v ON_ERROR_STOP=1 -c \
    "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"); then
    fail "Nie można połączyć się z lokalnym PostgreSQL przez $PGHOST."
fi
[[ "$installed_vector" == 1 ]] ||
    fail "Rozszerzenie pgvector jest niedostępne. Zainstaluj je dla wersji PostgreSQL użytej przez klaster."

if ! demo_exists=$(psql -X -d postgres -At -v ON_ERROR_STOP=1 -c \
    "SELECT 1 FROM pg_database WHERE datname = 'prawdowid'"); then
    fail "Nie udało się sprawdzić, czy baza prawdowid istnieje."
fi
if [[ "$demo_exists" != 1 ]]; then
    createdb prawdowid || fail "Nie udało się utworzyć bazy prawdowid."
fi

env_file=()
if [[ -f .env ]]; then
    env_file=(--env-file .env)
fi

extras=()
if [[ "$mode" == --discord ]]; then
    extras=(--extra discord)
fi

uv run --locked "${extras[@]}" python manage.py migrate --noinput ||
    fail "Nie udało się zastosować migracji. Sprawdź komunikat uv lub Django powyżej."
uv run --locked "${extras[@]}" python manage.py load_demo_corpus ||
    fail "Nie udało się odtworzyć korpusu. Sprawdź pliki korpusu i wersję pg_restore."
uv run --locked "${extras[@]}" python manage.py audit_corpus --check ||
    fail "Baza demonstracyjna nie przeszła audytu korpusu."

flock -u 9
exec 9>&-

if [[ "$mode" == --prepare ]]; then
    echo "Baza demonstracyjna gotowa. PGHOST=$PGHOST PGPORT=$PGPORT PGDATABASE=$PGDATABASE"
elif [[ "$mode" == --discord ]]; then
    exec uv run --locked "${extras[@]}" "${env_file[@]}" python manage.py run_discord
else
    exec uv run --locked "${env_file[@]}" python manage.py serve_demo --noreload "${PRAWDOWID_DEMO_BIND:-127.0.0.1:8000}"
fi
