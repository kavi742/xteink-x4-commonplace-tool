#!/usr/bin/env bash
#
# ## Preview deployments
#
# Reap every preview whose branch no longer exists on origin. Takes no
# arguments and is safe to run from a nightly job.
#
#   scripts/preview-gc.sh
#
# What it will and will not touch
# -------------------------------
# A directory under /srv/docker/data/xteink-previews is only a reap candidate
# when ALL of these hold:
#
#   1. it sits directly under the previews root, and
#   2. it contains a .preview-branch file written by preview-deploy.sh, and
#   3. re-deriving the slug from that branch name reproduces the directory
#      name exactly, and
#   4. the derived compose project carries the "xtk-" prefix.
#
# Anything it cannot positively identify on all four counts is reported and
# left alone. The production stack (project xteink-x4-commonplace-tool, data
# under /srv/docker/data/xteink-x4-commonplace and the Syncthing vault) fails
# both the prefix test and the location test, so it can never be a candidate.
#
# If `git ls-remote --heads origin` fails or returns nothing, the run aborts
# without reaping anything: a network blip or a bad credential must not be
# allowed to look like "every branch was deleted".
#
# Deletion is delegated to preview-teardown.sh, so the path guard lives in one
# place.
#
# Output discipline: diagnostics go to stderr; stdout gets one
# "REAPED <branch> <slug>" line per preview removed.
set -euo pipefail

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------

PREVIEW_ROOT="/srv/docker/data/xteink-previews"
PREVIEW_PORT_MIN=8100
PREVIEW_PORT_MAX=8179

# Named only so the guard below can be read without cross-referencing
# docker-compose.yml.
PROD_PROJECT="xteink-x4-commonplace-tool"

# ----------------------------------------------------------------------------
# Derived values. Keep byte-identical with preview-deploy.sh and
# preview-teardown.sh: all three must agree on the slug, project and data dir
# or they will operate on different stacks. The block is duplicated rather than
# sourced so each script stays self-contained, which is why
# preview_preferred_port is present here even though only preview-deploy.sh
# calls it.
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

log() { printf '[preview-gc] %s\n' "$*" >&2; }
die() { printf '[preview-gc] ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -eq 0 ] || die "usage: $(basename "$0")   (takes no arguments)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"
command -v git    >/dev/null 2>&1 || die "git not found on PATH"
[ -f "$SCRIPT_DIR/preview-teardown.sh" ] || die "preview-teardown.sh not found next to this script"

if [ ! -d "$PREVIEW_ROOT" ]; then
  log "summary: no previews root at $PREVIEW_ROOT, nothing to reap"
  exit 0
fi

LIVE_BRANCHES="$(git ls-remote --heads origin 2>/dev/null | awk '{print $2}' | sed -e 's|^refs/heads/||' || true)"
[ -n "$LIVE_BRANCHES" ] \
  || die "git ls-remote --heads origin returned nothing; refusing to reap on an unreliable branch list"
LIVE_COUNT="$(printf '%s\n' "$LIVE_BRANCHES" | wc -l | tr -d ' ')"
log "origin has $LIVE_COUNT branches"

branch_is_live() {
  printf '%s\n' "$LIVE_BRANCHES" | grep -Fxq -- "$1"
}

CHECKED=0
REAPED=0
SKIPPED=0

for dir in "$PREVIEW_ROOT"/*/; do
  [ -d "$dir" ] || continue
  dir="${dir%/}"
  slug="$(basename "$dir")"
  CHECKED=$(( CHECKED + 1 ))

  if [ ! -f "$dir/.preview-branch" ]; then
    SKIPPED=$(( SKIPPED + 1 ))
    log "skip '$slug': no .preview-branch marker, cannot positively identify it as a preview"
    continue
  fi

  branch="$(head -n 1 "$dir/.preview-branch" | tr -d '\r\n' || true)"
  if [ -z "$branch" ]; then
    SKIPPED=$(( SKIPPED + 1 ))
    log "skip '$slug': .preview-branch is empty"
    continue
  fi

  if [ "$(preview_slug "$branch")" != "$slug" ]; then
    SKIPPED=$(( SKIPPED + 1 ))
    log "skip '$slug': branch '$branch' re-derives to '$(preview_slug "$branch")', marker and directory disagree"
    continue
  fi

  project="$(preview_project "$branch")"
  case "$project" in
    xtk-?*) ;;
    *) SKIPPED=$(( SKIPPED + 1 )); log "skip '$slug': derived project '$project' lacks the xtk- prefix"; continue ;;
  esac
  if [ "$project" = "$PROD_PROJECT" ]; then
    SKIPPED=$(( SKIPPED + 1 ))
    log "skip '$slug': derived project is the production project, refusing"
    continue
  fi

  if branch_is_live "$branch"; then
    log "keep '$slug': branch '$branch' still exists on origin"
    continue
  fi

  log "reap '$slug': branch '$branch' no longer exists on origin"
  bash "$SCRIPT_DIR/preview-teardown.sh" "$branch"
  REAPED=$(( REAPED + 1 ))
  printf 'REAPED %s %s\n' "$branch" "$slug"
done

# Compose projects that look like previews but have no data directory are
# reported, never removed: that is only one of the two required signals.
ORPHAN_PROJECTS="$(docker ps -a --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null \
  | sort -u | grep '^xtk-' || true)"
for project in $ORPHAN_PROJECTS; do
  if [ ! -d "$PREVIEW_ROOT/${project#xtk-}" ]; then
    log "note: compose project '$project' has no data directory under $PREVIEW_ROOT, leaving it alone (remove by hand if stale)"
  fi
done

log "summary: checked $CHECKED preview directories, reaped $REAPED, skipped $SKIPPED as unidentifiable"
