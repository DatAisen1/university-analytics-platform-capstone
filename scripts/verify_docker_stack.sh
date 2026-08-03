#!/usr/bin/env bash
#
# scripts/verify_docker_stack.sh
#
# Task 54: verify the Docker stack from a clean start. This does more than
# print `docker compose ps` once -- a container can show "running" for a
# long time before its healthcheck ever reports "healthy" (or never does,
# if something's actually broken), so this polls each service's real health
# status and fails loudly with a clear, per-service reason if any of them
# don't reach "healthy" within a timeout, instead of a human eyeballing a
# single ps snapshot and assuming everything is fine.
#
# Usage:
#   docker compose -f docker/docker-compose.yml --env-file .env down -v
#   docker compose -f docker/docker-compose.yml --env-file .env up -d
#   bash scripts/verify_docker_stack.sh
# (or just: make clean-start)
#
# Dagster note: Dagster is intentionally NOT a docker-compose service in
# this project (see docker-compose.yml's header comment) -- it's run via
# the `dagster` CLI directly against the host Python environment, not
# containerized. So "Dagster healthy" from Task 54 is verified differently
# below: by validating its asset-graph Definitions load cleanly, which is
# the Dagster-native equivalent of a healthcheck for a CLI-driven
# orchestrator with no long-running server process to probe.

set -uo pipefail

COMPOSE="docker compose -f docker/docker-compose.yml --env-file .env"
TIMEOUT_SECONDS=90
POLL_INTERVAL=3
REQUIRED_CONTAINERS=("uap_postgres" "uap_minio")

failures=0

echo "== docker compose ps (point-in-time) =="
$COMPOSE ps
echo

for container in "${REQUIRED_CONTAINERS[@]}"; do
    echo "== Waiting for ${container} to report healthy (timeout ${TIMEOUT_SECONDS}s) =="
    elapsed=0
    status="starting"
    while [ "$elapsed" -lt "$TIMEOUT_SECONDS" ]; do
        # docker inspect, not docker compose ps, is the authoritative source
        # for the healthcheck's actual current state -- ps output formatting
        # varies across Compose versions and is meant for humans, not scripts.
        status=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container" 2>/dev/null)
        if [ -z "$status" ]; then
            status="not-found"
        fi
        if [ "$status" = "healthy" ]; then
            echo "  ${container}: healthy (after ${elapsed}s)"
            break
        fi
        if [ "$status" = "not-found" ]; then
            echo "  ${container}: container does not exist -- did 'docker compose up -d' run?"
            break
        fi
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
    done

    if [ "$status" != "healthy" ]; then
        echo "  FAIL: ${container} did not reach 'healthy' within ${TIMEOUT_SECONDS}s (last status: ${status})"
        echo "  Recent logs for ${container}:"
        docker logs --tail 30 "$container" 2>&1 | sed 's/^/    /'
        failures=$((failures + 1))
    fi
    echo
done

echo "== Postgres: verify it actually accepts a connection (not just 'healthy') =="
if docker exec uap_postgres pg_isready -U "${POSTGRES_USER:-uap_admin}" -d "${POSTGRES_DB:-university_analytics}" >/dev/null 2>&1; then
    echo "  OK: pg_isready succeeded"
else
    echo "  FAIL: pg_isready did not succeed against uap_postgres"
    failures=$((failures + 1))
fi
echo

echo "== MinIO: verify the S3 API responds (not just the container healthcheck) =="
if curl -sf "http://localhost:${MINIO_API_PORT:-9000}/minio/health/live" >/dev/null; then
    echo "  OK: MinIO liveness endpoint responded"
else
    echo "  FAIL: MinIO liveness endpoint did not respond on port ${MINIO_API_PORT:-9000}"
    failures=$((failures + 1))
fi
echo

echo "== Dagster: not a docker-compose service here -- verify its Definitions load instead =="
if command -v dagster >/dev/null 2>&1; then
    if dagster definitions validate -f orchestration/definitions.py >/dev/null 2>&1; then
        echo "  OK: orchestration/definitions.py loads and validates"
    else
        echo "  FAIL: 'dagster definitions validate' failed -- run it directly to see the error"
        failures=$((failures + 1))
    fi
else
    echo "  SKIPPED: 'dagster' CLI not found on PATH (pip install -r requirements.txt first)"
fi
echo

if [ "$failures" -eq 0 ]; then
    echo "RESULT: all services verified healthy."
    exit 0
else
    echo "RESULT: ${failures} check(s) failed -- see above."
    exit 1
fi