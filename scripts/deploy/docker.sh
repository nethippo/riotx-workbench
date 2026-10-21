#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
CONFIG="$ROOT/deploy/docker"
command -v docker >/dev/null || { echo 'Docker is required.' >&2; exit 1; }
compose() { docker compose --env-file "$CONFIG/.env" -f "$CONFIG/compose.yaml" "$@"; }
case "${1:-help}" in
  init)
    [[ ! -e "$CONFIG/.env" ]] || { echo '.env already exists; kept unchanged.'; exit 0; }
    command -v openssl >/dev/null || { echo 'OpenSSL is required for token generation.' >&2; exit 1; }
    umask 077
    deploy_token=$(openssl rand -hex 32)
    sed "s/^WORKBENCH_TOKEN=CHANGE_ME$/WORKBENCH_TOKEN=$deploy_token/" "$CONFIG/.env.example" > "$CONFIG/.env"
    echo 'Created deploy/docker/.env (0600). Read WORKBENCH_TOKEN there for UI access.'
    ;;
  up|build|status|logs|stop)
    [[ -f "$CONFIG/.env" ]] || { echo 'Run scripts/deploy/docker.sh init first.' >&2; exit 1; }
    if ! grep -Eq '^WORKBENCH_TOKEN=.{32,}$' "$CONFIG/.env"; then
      echo 'Set a non-placeholder WORKBENCH_TOKEN of at least 32 characters.' >&2; exit 1
    fi
    case "$1" in
      up) compose up -d --build ;;
      build) compose build ;;
      status) compose ps ;;
      logs) compose logs --tail=100 -f workbench ;;
      stop) compose down ;; # Keep the named data volume; never add -v.
    esac
    ;;
  *) echo 'Usage: scripts/deploy/docker.sh {init|up|build|status|logs|stop}' ;;
esac
