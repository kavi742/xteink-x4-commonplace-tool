#!/usr/bin/env bash
#
# ## Preview deployments
#
# Build and deploy the per-branch preview stack for one branch. Idempotent:
# re-running redeploys in place, keeping the same port, the same data
# directory, and the test data already in it.
#
#   scripts/preview-deploy.sh <branch>
#
# Run it from a checkout that already has <branch> checked out (this is what
# Jenkins does). The build context is the working tree, so the preview runs
# that branch's code; the branch argument only drives the derived names.
#
# Output discipline: every diagnostic goes to stderr. stdout carries exactly
# one line, printed last:
#
#   PREVIEW_URL=http://<host>:<port>
#
# so a pipeline can capture it with `returnStdout: true` and trim it.
#
# Safety: the preview stack runs with WATCHER_ENABLED=false and no avahi
# socket, so it can never reach the device, and it only ever bind-mounts
# directories under /srv/docker/data/xteink-previews. Production state is read
# once, read-only, to seed a fresh preview. See docker-compose.preview.yml.
#
# Environment overrides:
#   PREVIEW_HOST     hostname used in PREVIEW_URL (default: this machine's FQDN)
#   PROD_STATE_DIR   where to read production state.db / koreader.db from
#                    (default: the canonical host path, falling back to
#                    /prod-state, which is where the jenkins service mounts it)
#   PROD_VAULT_DIR   where to read the production vault from
set -euo pipefail

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------

PREVIEW_ROOT="/srv/docker/data/xteink-previews"
PREVIEW_PORT_MIN=8100
PREVIEW_PORT_MAX=8179

# Production sources, read only, used once per preview for seeding.
#
# These are overridable and PROD_STATE_DIR auto-falls back because the script
# runs in two contexts. On the host, the canonical paths apply. Inside the
# Jenkins container, the jenkins service in docker-compose.yml mounts
# production state read-only at /prod-state instead. The previews root needs no
# such handling: it is mounted at the same path inside and outside, which is
# what keeps the compose bind mounts valid from the host daemon's point of
# view.
PROD_STATE_DIR="${PROD_STATE_DIR:-}"
if [ -z "$PROD_STATE_DIR" ]; then
  if [ -r /srv/docker/data/xteink-x4-commonplace/state/state.db ]; then
    PROD_STATE_DIR="/srv/docker/data/xteink-x4-commonplace/state"
  else
    PROD_STATE_DIR="/prod-state"
  fi
fi
PROD_VAULT_DIR="${PROD_VAULT_DIR:-/srv/docker/data/syncthing/shares/xteink-vault}"

# Vault seeding caps. The prod vault is about 12 MB today, so a straight copy
# is free; these exist so a future 5 GB vault cannot fill the disk one preview
# at a time.
#   <= VAULT_FULL_COPY_MAX_MB   copy the whole vault, images included
#   <= VAULT_META_COPY_MAX_MB   copy markdown only, images skipped
#   above that                  skip the vault entirely
VAULT_FULL_COPY_MAX_MB=512
VAULT_META_COPY_MAX_MB=4096

# ----------------------------------------------------------------------------
# Derived values. Keep byte-identical with preview-teardown.sh and
# preview-gc.sh: all three must agree on the slug, project and data dir or they
# will operate on different stacks.
# ----------------------------------------------------------------------------

preview_sha1() {
  # No trailing newline, so the digest is stable and reproducible by hand.
  printf '%s' "$1" | sha1sum | cut -d' ' -f1
}

preview_slug() {
  local branch="$1" base hex
  base=$(printf '%s' "$branch" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9]/-/g' -e 's/--*/-/g' -e 's/^-*//' -e 's/-*$//')
  base="${base:0:20}"
  # Truncation can expose a trailing dash, which would double up against the
  # hash separator ("feature-" + "-a1b2c3").
  base="$(printf '%s' "$base" | sed -e 's/-*$//')"
  [ -n "$base" ] || base="branch"
  hex="$(preview_sha1 "$branch")"
  printf '%s-%s' "$base" "${hex:0:6}"
}

preview_project()  { printf 'xtk-%s' "$(preview_slug "$1")"; }
preview_data_dir() { printf '%s/%s' "$PREVIEW_ROOT" "$(preview_slug "$1")"; }

# Preferred port: hex digits 7-10 of sha1(branch), modulo the range width.
# Digits 1-6 are already spent on the slug suffix, so this keeps the two
# derivations from moving together.
preview_preferred_port() {
  local hex span
  hex="$(preview_sha1 "$1")"
  span=$(( PREVIEW_PORT_MAX - PREVIEW_PORT_MIN + 1 ))
  printf '%s' "$(( PREVIEW_PORT_MIN + (16#${hex:6:4} % span) ))"
}

# ----------------------------------------------------------------------------

log() { printf '[preview-deploy] %s\n' "$*" >&2; }
die() { printf '[preview-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
first_line() { printf '%s' "$1" | head -n 1; }

[ $# -eq 1 ] || die "usage: $(basename "$0") <branch>"
BRANCH="$1"
[ -n "$BRANCH" ] || die "branch must not be empty"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"
[ -f docker-compose.preview.yml ] || die "docker-compose.preview.yml not found in $REPO_ROOT"

SLUG="$(preview_slug "$BRANCH")"
PROJECT="$(preview_project "$BRANCH")"
DATA="$(preview_data_dir "$BRANCH")"

# Non-fatal: warn if the working tree is not the branch being deployed, since
# the image is built from the tree, not from the branch name.
HEAD_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
if [ "$HEAD_BRANCH" != "$BRANCH" ]; then
  log "WARNING: working tree is on '$HEAD_BRANCH' but deploying as '$BRANCH'; building '$HEAD_BRANCH' source"
fi

# ----------------------------------------------------------------------------
# Port selection
#
# Preferred port is deterministic from sha1(branch). Collision behaviour: if
# that port is already bound by a DIFFERENT compose project (or by any other
# host listener), probe forward through 8100-8179, wrapping at the top, and
# take the first free one. A port already published by THIS project is not a
# collision, it is a redeploy, so it is kept. The chosen port is recorded in
# <data>/.preview-port and reused on every later redeploy, so a preview URL
# stays stable even while the stack is down and the port looks free to others.
# If all 80 ports are taken, the deploy fails rather than stealing one.
# ----------------------------------------------------------------------------

ports_used_by_other_projects() {
  docker ps --format '{{.Label "com.docker.compose.project"}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v me="$PROJECT" '$1 != me { print $2 }' \
    | tr ',' '\n' \
    | sed -n 's/.*:\([0-9][0-9]*\)->.*/\1/p' \
    | sort -u || true
}

ports_used_by_this_project() {
  docker ps --format '{{.Label "com.docker.compose.project"}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v me="$PROJECT" '$1 == me { print $2 }' \
    | tr ',' '\n' \
    | sed -n 's/.*:\([0-9][0-9]*\)->.*/\1/p' \
    | sort -u || true
}

host_is_listening() {
  command -v ss >/dev/null 2>&1 || return 1
  [ -n "$(ss -Hltn "sport = :$1" 2>/dev/null)" ]
}

OTHER_PORTS="$(ports_used_by_other_projects)"
OUR_PORTS="$(ports_used_by_this_project)"

port_available() {
  local p="$1"
  if printf '%s\n' "$OTHER_PORTS" | grep -qx "$p"; then return 1; fi
  if printf '%s\n' "$OUR_PORTS"   | grep -qx "$p"; then return 0; fi
  if host_is_listening "$p"; then return 1; fi
  return 0
}

PORT=""
PORT_FILE="$DATA/.preview-port"
if [ -f "$PORT_FILE" ]; then
  RECORDED="$(tr -cd '0-9' < "$PORT_FILE" || true)"
  if [ -n "$RECORDED" ] \
     && [ "$RECORDED" -ge "$PREVIEW_PORT_MIN" ] \
     && [ "$RECORDED" -le "$PREVIEW_PORT_MAX" ] \
     && port_available "$RECORDED"; then
    PORT="$RECORDED"
    log "reusing recorded port $PORT for $SLUG"
  elif [ -n "$RECORDED" ]; then
    log "recorded port $RECORDED is unusable, re-probing"
  fi
fi

if [ -z "$PORT" ]; then
  PREFERRED="$(preview_preferred_port "$BRANCH")"
  SPAN=$(( PREVIEW_PORT_MAX - PREVIEW_PORT_MIN + 1 ))
  for (( i = 0; i < SPAN; i++ )); do
    CANDIDATE=$(( PREVIEW_PORT_MIN + ((PREFERRED - PREVIEW_PORT_MIN + i) % SPAN) ))
    if port_available "$CANDIDATE"; then
      PORT="$CANDIDATE"
      break
    fi
  done
  [ -n "$PORT" ] || die "no free port in ${PREVIEW_PORT_MIN}-${PREVIEW_PORT_MAX}; tear down a stale preview first"
  if [ "$PORT" != "$PREFERRED" ]; then
    log "preferred port $PREFERRED is taken, using $PORT instead"
  fi
fi

# ----------------------------------------------------------------------------
# Seeding (first deploy only)
#
# Never runs on a redeploy: overwriting the state DB would silently discard
# whatever was created while testing the preview.
# ----------------------------------------------------------------------------

seed_db() {
  local src="$1" dest="$2" label="$3" err
  if [ ! -r "$src" ]; then
    log "seed: $label unreadable at $src, preview starts with an empty $label"
    return 0
  fi
  if ! command -v sqlite3 >/dev/null 2>&1; then
    log "seed: sqlite3 CLI not installed, preview starts with an empty $label"
    return 0
  fi
  rm -f "$dest"
  # VACUUM INTO, not cp. Both production DBs run journal_mode=delete, so a
  # plain copy of a live DB can capture a write in progress and produce a torn
  # file. VACUUM INTO takes a transactionally consistent hot snapshot.
  # -readonly guarantees we cannot write to production even if a hot journal is
  # present; the trade is that a rollback-pending DB fails to seed, which is
  # logged and left empty rather than repaired behind production's back.
  if err="$(sqlite3 -readonly "$src" "VACUUM INTO '$dest'" 2>&1)"; then
    log "seed: $label snapshotted from production via VACUUM INTO"
  else
    rm -f "$dest"
    log "seed: $label snapshot failed (${err:-unknown error}), preview starts with an empty $label"
  fi
}

seed_vault() {
  local src="$1" dest="$2" mb err
  if [ ! -d "$src" ]; then
    log "seed: production vault not found at $src, preview vault starts empty"
    return 0
  fi
  mb="$(du -sm "$src" 2>/dev/null | cut -f1 || true)"
  if [ -z "$mb" ]; then
    log "seed: could not size the production vault, preview vault starts empty"
    return 0
  fi
  # Copied, never symlinked: a symlink would let the preview write into the
  # Syncthing share and propagate junk to every synced device. Ownership is not
  # preserved, so this works as a non-root user against root-owned files.
  if [ "$mb" -le "$VAULT_FULL_COPY_MAX_MB" ]; then
    if err="$(cp -R --preserve=mode,timestamps "$src/." "$dest/" 2>&1)"; then
      log "seed: vault copied in full (${mb} MB)"
    else
      log "seed: vault copy incomplete (${mb} MB): $(first_line "$err")"
    fi
  elif [ "$mb" -le "$VAULT_META_COPY_MAX_MB" ]; then
    err="$( cd "$src" && find . -type f -name '*.md' \
        -exec cp --preserve=mode,timestamps --parents {} "$dest/" \; 2>&1 )" || true
    log "seed: vault is ${mb} MB (over ${VAULT_FULL_COPY_MAX_MB} MB), copied markdown only, images skipped. $(first_line "$err")"
  else
    log "seed: vault is ${mb} MB (over ${VAULT_META_COPY_MAX_MB} MB), skipped entirely, preview vault starts empty"
  fi
}

SEEDED_MARKER="$DATA/.preview-seeded"
ACTION="redeployed"
mkdir -p "$DATA/vault" "$DATA/config" "$DATA/state"
if [ -f "$SEEDED_MARKER" ]; then
  log "existing preview at $DATA, keeping its data"
else
  ACTION="created"
  log "first deploy for '$BRANCH', seeding $DATA from production"
  log "seed: state source $PROD_STATE_DIR, vault source $PROD_VAULT_DIR"
  seed_db "$PROD_STATE_DIR/state.db"    "$DATA/state/state.db"    "state.db"
  seed_db "$PROD_STATE_DIR/koreader.db" "$DATA/state/koreader.db" "koreader.db"
  seed_vault "$PROD_VAULT_DIR" "$DATA/vault"
  date -Is > "$SEEDED_MARKER"
fi

printf '%s\n' "$BRANCH" > "$DATA/.preview-branch"
printf '%s\n' "$PORT"   > "$PORT_FILE"

# ----------------------------------------------------------------------------
# Deploy
# ----------------------------------------------------------------------------

export PREVIEW_PROJECT="$PROJECT"
export PREVIEW_PORT="$PORT"
export PREVIEW_DATA="$DATA"
export PREVIEW_BRANCH="$BRANCH"

log "building and starting project $PROJECT on port $PORT (source $HEAD_BRANCH@$HEAD_SHA)"
docker compose -f docker-compose.preview.yml -p "$PROJECT" up -d --build --remove-orphans >&2

HOST="${PREVIEW_HOST:-$(hostname -f 2>/dev/null || hostname)}"

log "summary: $ACTION preview for '$BRANCH' as project $PROJECT on port $PORT, data $DATA, watcher disabled"
printf 'PREVIEW_URL=http://%s:%s\n' "$HOST" "$PORT"
