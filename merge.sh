#!/usr/bin/env bash
# Builds telegram-core in a fresh checkout: upstream main with every branch in
# the list merged on top, in order.
#
# The bot needs core changes that are still open as upstream PRs. Instead of
# keeping a fork that drifts, the core is rebuilt from scratch each time, so it
# is always "main plus our open PRs" and nothing else. Branches are merged, not
# rebased, so the PR branches themselves are never rewritten. A branch whose PR
# is merged upstream drops out on its own.
#
#   ./merge.sh branches.txt /tmp/core
#
# Leaves the result checked out (detached) in the target directory and prints
# what went in. Exits non-zero on a merge conflict, naming the branch and files.
# Under GitHub Actions it also writes `base`, `head` and `summary` outputs.
set -euo pipefail

list=$(realpath "$1")
dir=$2

upstream=${UPSTREAM:-https://github.com/singnet/Omega.git}
upstream_ref=${UPSTREAM_REF:-main}
fork=${FORK:-https://github.com/iCog-Labs-Dev/Omega.git}

git init -q "$dir"
cd "$dir"
git config user.name "telegram-core-recipe"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git remote add upstream "$upstream"
git remote add fork "$fork"

# Full history: a merge needs the common ancestor of main and each branch.
git fetch -q upstream "+refs/heads/${upstream_ref}:refs/remotes/upstream/${upstream_ref}"
base=$(git rev-parse "upstream/${upstream_ref}")
git checkout -q --detach "$base"

summary="base: ${upstream} ${upstream_ref} ${base}"$'\n'

# The PR state, or "" when the line names no PR or gh cannot tell.
pr_state() {
    local pr=$1
    [ -n "$pr" ] || return 0
    gh pr view "${pr##*#}" --repo "${pr%#*}" --json state --jq .state 2>/dev/null || true
}

while read -r branch pr _; do
    case "$branch" in ''|'#'*) continue ;; esac
    git fetch -q fork "+refs/heads/${branch}:refs/remotes/fork/${branch}"
    sha=$(git rev-parse "fork/${branch}")

    if git merge-base --is-ancestor "$sha" "$base"; then
        summary+="skipped ${branch} ${sha}: already in ${upstream_ref}"$'\n'
        continue
    fi
    if [ "$(pr_state "${pr:-}")" = "MERGED" ]; then
        summary+="skipped ${branch} ${sha}: ${pr} is merged"$'\n'
        continue
    fi

    if ! git merge -q --no-ff -m "Merge ${branch} (${sha})${pr:+ for ${pr}}" "$sha"; then
        echo "::error::${branch} (${sha}) conflicts with the core built so far:"
        git diff --name-only --diff-filter=U
        exit 1
    fi
    summary+="merged  ${branch} ${sha}${pr:+ (${pr})}"$'\n'
done < "$list"

head=$(git rev-parse HEAD)
summary+="head: ${head}"$'\n'
printf '%s' "$summary"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
        echo "base=${base}"
        echo "head=${head}"
        echo "summary<<EOF"
        printf '%s' "$summary"
        echo "EOF"
    } >> "$GITHUB_OUTPUT"
fi
