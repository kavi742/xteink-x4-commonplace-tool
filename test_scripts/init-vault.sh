#!/usr/bin/env bash
# Creates the Obsidian vault folder structure and a Syncthing .stignore
# file inside it. Safe to re-run — mkdir -p and the heredoc write are
# both idempotent (ponytail: no need for a "does this exist" check when
# the underlying operations already are).
#
# Usage: ./scripts/init-vault.sh [path-to-vault]
# Defaults to ./vault (the path docker-compose.yml mounts as /data/vault).

set -euo pipefail

VAULT_DIR="${1:-./vault}"

mkdir -p \
  "$VAULT_DIR/Reading Log" \
  "$VAULT_DIR/Books"

# Syncthing ignore patterns for an Obsidian vault. Obsidian's own
# workspace/cache files are device-local and cause needless sync
# conflicts if synced between machines — everything else (including
# .obsidian/ config, so plugins/settings stay consistent) is left alone.
cat > "$VAULT_DIR/.stignore" <<'EOF'
.obsidian/workspace*
.obsidian/cache
.trash
.DS_Store
Thumbs.db
EOF

echo "Vault structure ready at: $VAULT_DIR"
find "$VAULT_DIR" -mindepth 1 -maxdepth 1
