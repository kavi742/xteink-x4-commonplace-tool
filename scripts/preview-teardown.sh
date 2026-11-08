#!/usr/bin/env bash
#
# ## Preview deployments
#
# Stop and remove one branch's preview stack and delete its data directory.
#
#   scripts/preview-teardown.sh <branch>
#
# Idempotent and safe to call on a branch that was never deployed: if the
# project and the data directory are both absent it logs that and exits 0.
#
# Output discipline: diagnostics go to stderr, stdout stays empty. The caller
# only cares about the exit status.
#
# Deletion safety: the data directory is resolved with `readlink -f` and must
# end up strictly BELOW /srv/docker/data/xteink-previews. An empty slug, a slug
# of "/", a slug containing a path separator, or a symlink pointing anywhere
# else all abort the run before anything is removed. Nothing outside the
# previews root is ever a candidate for deletion.
set -euo pipefail

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------

PREVIEW_ROOT="/srv/docker/data/xteink-previews"
PREVIEW_PORT_MIN=8100
PREVIEW_PORT_MAX=8179

# ----------------------------------------------------------------------------
# Derived values. Keep byte-identical with preview-deploy.sh and preview-gc.sh:
# all three must agree on the slug, project and data dir or they will operate
# on different stacks. The block is duplicated rather than sourced so each
# script stays self-contained, which is why preview_preferred_port is present
# here even though only preview-deploy.sh calls it.
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

log() { printf '[preview-teardown] %s\n' "$*" >&2; }
die() { printf '[preview-teardown] ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: $(basename "$0") <branch>"
BRANCH="$1"
[ -n "$BRANCH" ] || die "branch must not be empty"

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"

SLUG="$(preview_slug "$BRANCH")"
PROJECT="$(preview_project "$BRANCH")"
DATA="$(preview_data_dir "$BRANCH")"

# ----------------------------------------------------------------------------
# Deletion guard. Everything below rm -rf depends on this being correct, so it
# refuses on anything it is not certain about rather than carrying on.
# ----------------------------------------------------------------------------

assert_removable_preview_dir() {
  local dir="$1" slug="$2" resolved root

  [ -n "$slug" ]              || die "refusing to delete: empty slug"
  [ "$slug" != "/" ]          || die "refusing to delete: slug is '/'"
  [ "$slug" != "." ]          || die "refusing to delete: slug is '.'"
  [ "$slug" != ".." ]         || die "refusing to delete: slug is '..'"
  case "$slug" in
    */*|*..*) die "refusing to delete: slug '$slug' contains a path separator or '..'" ;;
  esac

  [ -n "$dir" ]      || die "refusing to delete: empty data directory"
  [ "$dir" != "/" ]  || die "refusing to delete: data directory is '/'"

  root="$(readlink -f "$PREVIEW_ROOT" 2>/dev/null || true)"
  [ -n "$root" ] && [ "$root" != "/" ] \
    || die "refusing to delete: previews root '$PREVIEW_ROOT' did not resolve"

  resolved="$(readlink -f "$dir" 2>/dev/null || true)"
  [ -n "$resolved" ] || die "refusing to delete: '$dir' did not resolve"
  [ "$resolved" != "/" ]     || die "refusing to delete: '$dir' resolved to '/'"
  [ "$resolved" != "$root" ] || die "refusing to delete: '$dir' resolved to the previews root itself"

  # Must be strictly below the root. A symlink out of the previews tree lands
  # here and is refused.
  case "$resolved" in
    "$root"/?*) ;;
    *) die "refusing to delete: '$dir' resolves to '$resolved', outside $root" ;;
  esac
}

case "$PROJECT" in
  xtk-?*) ;;
  *) die "refusing to act: derived project '$PROJECT' is not a preview project" ;;
esac

REMOVED_STACK="no"
REMOVED_DATA="no"

# `docker compose -p <name> down` works without a compose file: compose finds
# the resources by project label. It also exits 0 on a project that never
# existed, so ask the daemon first to get an honest summary line.
if [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)" ]; then
  docker compose -p "$PROJECT" down --remove-orphans --volumes 1>&2
  REMOVED_STACK="yes"
else
  log "no containers for compose project '$PROJECT'"
fi

if [ -d "$DATA" ]; then
  assert_removable_preview_dir "$DATA" "$SLUG"
  rm -rf -- "$DATA"
  REMOVED_DATA="yes"
else
  log "no data directory at $DATA"
fi

if [ "$REMOVED_STACK" = "no" ] && [ "$REMOVED_DATA" = "no" ]; then
  log "summary: nothing to tear down for '$BRANCH' (project $PROJECT, data $DATA)"
else
  log "summary: tore down preview for '$BRANCH' (project $PROJECT, stack removed=$REMOVED_STACK, data removed=$REMOVED_DATA, $DATA)"
fi
exit 0
