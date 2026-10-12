#!/usr/bin/env bash
# Start the real local website, not the sample visual fixtures.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null || { echo 'Install Docker Engine and its Compose plugin first.' >&2; exit 1; }
if [[ "${1:-up}" != down ]]; then
    python3 scripts/configure_local_docker.py
fi
if [[ ! -f docker/local/compose.env ]]; then
    echo 'Local Docker config is missing; run scripts/local-stack.sh up --build -d first.' >&2
    exit 1
fi
# The private env file, not inherited shell credentials, coordinates the services.
unset COMPOSE_PROJECT_NAME LOCAL_UID LOCAL_GID POSTGRES_DB POSTGRES_PASSWORD FRONTEND_PORT BACKEND_PORT
if [[ $# == 0 ]]; then set -- up --build -d; fi
exec docker compose --env-file docker/local/compose.env -f docker-compose.local.yml "$@"
