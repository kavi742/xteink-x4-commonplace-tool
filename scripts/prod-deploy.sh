#!/usr/bin/env bash
#
# ## Preview deployments
#
# This is the production counterpart to the preview scripts, kept beside them
# so the two paths are obvious side by side. It is NOT a preview: it rebuilds
# and restarts the live stack defined in docker-compose.yml, with the real
# vault, the real state DB, the watcher enabled and the device attached.
#
#   scripts/prod-deploy.sh
#
# Preview equivalents, for contrast:
#   scripts/preview-deploy.sh <branch>     disposable, watcher off, port 81xx
#   scripts/preview-teardown.sh <branch>
#   scripts/preview-gc.sh
#
# Three things here are deliberate and must not be "simplified":
#
#   1. The compose project name is pinned. Compose otherwise derives it from
#      the current directory name, so running this from a Jenkins workspace
#      would spin up a SECOND production stack alongside the real one, both
#      bind-mounting the same vault and state DB.
#   2. Only the xteink service is rebuilt. docker-compose.yml also defines the
#      jenkins service; recreating that from inside a Jenkins job kills the job
#      that is running the deploy.
#   3. There is no --remove-orphans. With a single service named, compose would
#      treat the jenkins container as an orphan and remove it.
#
# Output discipline: diagnostics go to stderr, stdout stays empty.
set -euo pipefail

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------

PROD_PROJECT="xteink-x4-commonplace-tool"
PROD_SERVICE="xteink"
PROD_COMPOSE_FILE="docker-compose.yml"
HEALTH_TIMEOUT_SECONDS=90

# ----------------------------------------------------------------------------

log() { printf '[prod-deploy] %s\n' "$*" >&2; }
die() { printf '[prod-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -eq 0 ] || die "usage: $(basename "$0")   (takes no arguments)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"
[ -f "$PROD_COMPOSE_FILE" ] || die "$PROD_COMPOSE_FILE not found in $REPO_ROOT"

# docker-compose.yml interpolates ${KOSYNC_PASSWORD}. Compose reads it from the
# .env file in this directory, or from the environment. If it resolves to an
# empty string the deploy silently disables kosync auth on an endpoint that is
# published through nginx-proxy-manager, so refuse instead.
if [ -z "${KOSYNC_PASSWORD:-}" ] && ! grep -q '^KOSYNC_PASSWORD=..*' .env 2>/dev/null; then
  die "KOSYNC_PASSWORD is not set and $REPO_ROOT/.env does not define it; refusing to deploy with sync auth disabled"
fi

HEAD_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

log "rebuilding service '$PROD_SERVICE' in project '$PROD_PROJECT' from $HEAD_BRANCH@$HEAD_SHA"
docker compose -p "$PROD_PROJECT" -f "$PROD_COMPOSE_FILE" build "$PROD_SERVICE" 1>&2
docker compose -p "$PROD_PROJECT" -f "$PROD_COMPOSE_FILE" up -d "$PROD_SERVICE" 1>&2

CONTAINER="$(docker compose -p "$PROD_PROJECT" -f "$PROD_COMPOSE_FILE" ps -q "$PROD_SERVICE" 2>/dev/null | head -n 1 || true)"
[ -n "$CONTAINER" ] || die "service '$PROD_SERVICE' is not running after up -d"

# The image ships a HEALTHCHECK against /health, so wait for it rather than
# reporting success on a container that is crash looping.
STATUS="unknown"
for (( waited = 0; waited < HEALTH_TIMEOUT_SECONDS; waited += 3 )); do
  STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo unknown)"
  case "$STATUS" in
    healthy|none) break ;;
    *) sleep 3 ;;
  esac
done

if [ "$STATUS" = "healthy" ] || [ "$STATUS" = "none" ]; then
  log "summary: redeployed production service '$PROD_SERVICE' (project $PROD_PROJECT) from $HEAD_BRANCH@$HEAD_SHA, health=$STATUS"
else
  log "summary: redeployed production service '$PROD_SERVICE' (project $PROD_PROJECT) from $HEAD_BRANCH@$HEAD_SHA, but health=$STATUS after ${HEALTH_TIMEOUT_SECONDS}s"
  die "service '$PROD_SERVICE' did not become healthy; check 'docker compose -p $PROD_PROJECT logs $PROD_SERVICE'"
fi
