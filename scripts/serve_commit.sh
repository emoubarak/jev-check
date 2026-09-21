#!/usr/bin/env bash
# Serve your app as it was at <commit>: the "before" of a ticket's before/after pair.
#
#   serve_commit.sh <commit> [port]                 # default port 8081
#   jevcheck ask --base http://localhost:8081 --as free --shot before.png --goal "…"
#
# A throw-away git worktree in $TMPDIR (never your working tree), sharing the repo's
# dependencies and env files by symlink, so it talks to the same STAGING backend (set
# [target.local_guard] in jevcheck.toml to have that asserted). Ctrl-C stops the server; the
# links are unlinked (never followed) and the worktree removed on the way out.
#
#   SERVE_CMD   how to start the dev server; {port} is substituted
#               (default: npx vite --port {port} --strictPort)
#   SERVE_LINKS what to share from the repo (default: "node_modules .env .env.local")
set -euo pipefail
commit="${1:?usage: serve_commit.sh <commit> [port]}"
port="${2:-8081}"
serve_cmd="${SERVE_CMD:-npx vite --port {port} --strictPort}"
links="${SERVE_LINKS:-node_modules .env .env.local}"
repo="$(git rev-parse --show-toplevel)"
wt="$(mktemp -d "${TMPDIR:-/tmp}/jevcheck-at-XXXXXX")"

cleanup() {
  for link in $links; do [ -L "$wt/$link" ] && unlink "$wt/$link"; done
  git -C "$repo" worktree remove --force "$wt" 2>/dev/null || true
  git -C "$repo" worktree prune
}
trap cleanup EXIT INT TERM

git -C "$repo" worktree add --detach -q "$wt" "$commit"
for link in $links; do
  [ -e "$repo/$link" ] && ln -s "$repo/$link" "$wt/$link"
done
echo "serving $(git -C "$wt" log --oneline -1) on http://localhost:$port (Ctrl-C to stop)"
cd "$wt" && eval "${serve_cmd//\{port\}/$port}"
