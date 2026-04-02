#!/usr/bin/env bash
set -euo pipefail

SOURCE_REF="${1:-main}"
TARGET_REF="${2:-github-public/main}"
SYNC_BRANCH="${3:-public-sync}"
FORCE_RESET="${FORCE_RESET:-0}"

ALLOWLIST=(
  "README.md"
  "CHANGELOG.md"
  "pyproject.toml"
  "uv.lock"
  "inspire"
  "tests"
)

ensure_ref_exists() {
  local ref="$1"
  if ! git rev-parse --verify --quiet "$ref" >/dev/null; then
    echo "[error] Git ref not found: $ref" >&2
    exit 1
  fi
}

ensure_clean_tree() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "[error] Working tree has tracked changes. Commit/stash first." >&2
    exit 1
  fi
}

ensure_sync_branch_safe_to_reset() {
  if ! git show-ref --verify --quiet "refs/heads/${SYNC_BRANCH}"; then
    return
  fi

  local ahead
  ahead="$(git rev-list --count "${TARGET_REF}..${SYNC_BRANCH}")"
  if [[ "$ahead" == "0" ]] || [[ "$FORCE_RESET" == "1" ]]; then
    return
  fi

  echo "[error] Branch '${SYNC_BRANCH}' is ${ahead} commit(s) ahead of ${TARGET_REF}." >&2
  echo "        Refusing to reset it. Re-run with FORCE_RESET=1 if this is intentional." >&2
  exit 1
}

sync_allowlist_from_source() {
  local path
  for path in "${ALLOWLIST[@]}"; do
    git rm -r --quiet --ignore-unmatch -- "$path" || true
    if git cat-file -e "${SOURCE_REF}:${path}" 2>/dev/null; then
      git restore --source "$SOURCE_REF" --staged --worktree -- "$path"
    fi
  done
}

guard_no_stale_allowlist_files() {
  local source_files
  local index_files
  source_files="$(mktemp)"
  index_files="$(mktemp)"
  trap 'rm -f "${source_files:-}" "${index_files:-}"' RETURN

  git ls-tree -r --name-only "$SOURCE_REF" -- "${ALLOWLIST[@]}" | LC_ALL=C sort >"$source_files"
  git ls-files -- "${ALLOWLIST[@]}" | LC_ALL=C sort >"$index_files"

  if ! diff -u "$source_files" "$index_files" >/dev/null; then
    echo "[error] Public sync guard failed: stale or missing files in allowlist." >&2
    echo "        Example this prevents: inspire/platform/web/browser_api/notebooks.py" >&2
    diff -u "$source_files" "$index_files" || true
    exit 1
  fi
}

guard_lockfiles_when_inspire_changes() {
  local changed_inspire
  changed_inspire="$(git diff --name-only "$TARGET_REF" -- inspire)"
  if [[ -z "$changed_inspire" ]]; then
    return
  fi

  if ! git diff --quiet "$SOURCE_REF" -- pyproject.toml uv.lock; then
    echo "[error] Public sync guard failed: inspire/ changed but pyproject.toml or uv.lock" >&2
    echo "        does not match ${SOURCE_REF}." >&2
    exit 1
  fi
}

main() {
  ensure_clean_tree
  ensure_ref_exists "$SOURCE_REF"
  ensure_ref_exists "$TARGET_REF"
  ensure_sync_branch_safe_to_reset

  git checkout -B "$SYNC_BRANCH" "$TARGET_REF"
  sync_allowlist_from_source

  guard_no_stale_allowlist_files
  guard_lockfiles_when_inspire_changes

  echo "[ok] Sync prepared on branch '$SYNC_BRANCH'."
  echo "[ok] Allowlist: ${ALLOWLIST[*]}"
  echo "[next] Review with: git diff --stat ${TARGET_REF}..HEAD"
}

main "$@"
