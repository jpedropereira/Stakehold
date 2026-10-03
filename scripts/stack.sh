#!/usr/bin/env bash
# Convenience wrapper around the per-service docker compose commands documented in
# docs/smoke-test.md. Does not merge or replace any service's own docker-compose.yml --
# see that doc for why a merged root compose file was rejected (service-name collisions).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SERVICES=(auth data-store portfolio web)
HEALTH_NAMES=(auth portfolio data-store web)
HEALTH_URLS=(
  http://localhost:8001/health
  http://localhost:8002/health
  http://localhost:8082/health
  http://localhost:8000/health
)

compose() {
  local service="$1"
  shift
  docker compose \
    -f "services/${service}/docker-compose.yml" \
    -f "services/${service}/docker-compose.stakehold-net.yml" \
    "$@"
}

cmd_up() {
  if ! docker network inspect stakehold-net >/dev/null 2>&1; then
    echo "Creating stakehold-net network..."
    docker network create stakehold-net
  fi

  for service in "${SERVICES[@]}"; do
    if [[ ! -f "services/${service}/.env" ]]; then
      echo "services/${service}/.env is missing -- copy it from .env.example and fill in" \
        "real secrets before starting the stack (see docs/smoke-test.md)." >&2
      exit 1
    fi
  done

  for service in "${SERVICES[@]}"; do
    echo "Starting ${service}..."
    compose "$service" up -d "$@"
  done

  echo "Waiting for health endpoints..."
  local attempt=0
  until cmd_health; do
    attempt=$((attempt + 1))
    if ((attempt >= 15)); then
      echo "Services did not become healthy in time." >&2
      exit 1
    fi
    sleep 2
  done
}

cmd_down() {
  # Reverse of startup order, matching docs/smoke-test.md's teardown.
  for ((i = ${#SERVICES[@]} - 1; i >= 0; i--)); do
    service="${SERVICES[i]}"
    echo "Stopping ${service}..."
    compose "$service" down
  done
}

cmd_ps() {
  for service in "${SERVICES[@]}"; do
    echo "--- ${service} ---"
    compose "$service" ps
  done
}

cmd_health() {
  local failed=0
  echo "Checking health endpoints..."
  for i in "${!HEALTH_URLS[@]}"; do
    local url="${HEALTH_URLS[$i]}"
    local name="${HEALTH_NAMES[$i]}"
    if curl -sf -o /dev/null "$url"; then
      echo "  ${name}: OK (${url})"
    else
      echo "  ${name}: FAILED (${url})"
      failed=1
    fi
  done
  return "$failed"
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  down) cmd_down ;;
  ps) cmd_ps ;;
  health) cmd_health ;;
  *)
    echo "Usage: $0 {up|down|ps|health} [extra docker compose args for up, e.g. --build]" >&2
    exit 1
    ;;
esac
