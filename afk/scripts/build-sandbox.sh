#!/usr/bin/env bash
# Build AFK Pi Docker image. Matches Sandcastle UID/GID for bind-mounted worktrees.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${AFK_DOCKER_IMAGE:-sql-buddy-afk:pi}"
DOCKER_BIN="${DOCKER:-docker}"
UID_ARG="$(id -u)"
GID_ARG="$(id -g)"

if [[ ! -x "${DOCKER_BIN}" ]] && ! command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
  echo "docker not available (DOCKER=${DOCKER_BIN})" >&2
  exit 1
fi
if ! "${DOCKER_BIN}" info >/dev/null 2>&1; then
  echo "docker daemon not reachable" >&2
  exit 1
fi

echo "Building ${IMAGE} (AGENT_UID=${UID_ARG} AGENT_GID=${GID_ARG})"
"${DOCKER_BIN}" build \
  --build-arg "AGENT_UID=${UID_ARG}" \
  --build-arg "AGENT_GID=${GID_ARG}" \
  -t "${IMAGE}" \
  -f "${ROOT}/.sandcastle/Dockerfile" \
  "${ROOT}/.sandcastle"

echo "OK: ${IMAGE}"
"${DOCKER_BIN}" image inspect "${IMAGE}" --format 'id={{.Id}} created={{.Created}}'
