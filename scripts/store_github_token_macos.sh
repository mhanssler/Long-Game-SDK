#!/usr/bin/env bash
set -euo pipefail

# Store a GitHub fine-grained token in macOS Keychain for this repo without
# putting the token in shell history, chat logs, or the Git remote URL.
#
# Usage from repo root:
#   bash scripts/store_github_token_macos.sh

REPO_URL="https://github.com/mhanssler/Long-Game-SDK.git"
USERNAME="mhanssler"
HOST="github.com"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${repo_root}" ]]; then
  echo "Run this from inside the Long-Game-SDK git checkout." >&2
  exit 1
fi
cd "${repo_root}"

git remote set-url origin "${REPO_URL}"
git config --global credential.helper osxkeychain

printf "GitHub fine-grained token for %s: " "${USERNAME}" >&2
IFS= read -rs token
printf "\n" >&2

if [[ -z "${token}" ]]; then
  echo "No token entered; aborting." >&2
  exit 1
fi

printf "protocol=https\nhost=%s\nusername=%s\npassword=%s\n\n" "${HOST}" "${USERNAME}" "${token}" | git credential-osxkeychain store
unset token

echo "Stored GitHub token in macOS Keychain. Verifying read access..."
git ls-remote origin HEAD >/dev/null

echo "OK: GitHub auth works for ${REPO_URL}"
echo "Remote: $(git remote get-url origin)"
