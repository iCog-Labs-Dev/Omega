#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-prod-$(date +%Y-%m-%d)}"
TARGET="${2:-origin/telegram}"

git fetch --tags --prune origin

git tag -d "$TAG" 2>/dev/null || true
git push origin ":refs/tags/$TAG" 2>/dev/null || true

git tag "$TAG" "$TARGET"
git push origin "$TAG"

# The agent summarizes this release to users on its first start-up, and it
# reads the notes from the GitHub release for this tag. Publish that release
# before the deploy finishes, or the announcement is skipped until the next
# container start.

sleep 5
gh run list --repo iCog-Labs-Dev/mettaclaw --workflow=deploy-production.yml -L 1
