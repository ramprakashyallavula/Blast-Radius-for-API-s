#!/usr/bin/env bash
# Runs inside the BlastRadius GitHub Action (see action.yml).
#
# 1. Gets the spec as it is on the PR's base branch (what's live).
# 2. Collects consumers: local folders and/or other repositories.
# 3. Runs `blastradius check` against the spec in this PR.
# 4. Posts the report as a PR comment, updating our earlier comment if there is one.
#
# Inputs arrive as environment variables:
#   SPEC, CONSUMERS, CONSUMERS_DIR, CONSUMER_REPOS, CONSUMER_TOKEN, FAIL_ON, COMMENT,
#   BASE_REF, PR_NUMBER, GITHUB_REPOSITORY, GH_TOKEN
set -euo pipefail

MARKER='<!-- blastradius-report -->'
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

out() { echo "$1=$2" >> "${GITHUB_OUTPUT:-/dev/null}"; }
trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; echo "${s%"${s##*[![:space:]]}"}"; }

: "${SPEC:?the 'spec' input is required}"
FAIL_ON="${FAIL_ON:-impacted}"
COMMENT="${COMMENT:-true}"

# --- 1. The base version of the spec ---------------------------------------

if [ -z "${BASE_REF:-}" ]; then
  echo "::notice::Not a pull request, so there's no base version to compare against. Skipping."
  out exit-code 0
  exit 0
fi

git fetch --no-tags --depth=1 origin "$BASE_REF" >/dev/null 2>&1 || true
if ! git show "origin/$BASE_REF:$SPEC" > "$work/base-spec" 2>/dev/null; then
  echo "::notice::$SPEC doesn't exist on $BASE_REF yet (new API). Nothing to compare."
  out exit-code 0
  exit 0
fi

if [ ! -f "$SPEC" ]; then
  echo "::error::$SPEC was deleted in this pull request. Every consumer of it will break."
  out exit-code 1
  exit 1
fi

if cmp -s "$work/base-spec" "$SPEC"; then
  echo "::notice::$SPEC is unchanged in this pull request. Skipping."
  out exit-code 0
  exit 0
fi

# --- 2. Consumers ----------------------------------------------------------

args=()
while IFS= read -r line; do
  line="$(trim "$line")"
  [ -n "$line" ] && args+=(--consumer "$line")
done <<< "${CONSUMERS:-}"

if [ -n "$(trim "${CONSUMERS_DIR:-}")" ]; then
  args+=(--consumers-dir "$(trim "$CONSUMERS_DIR")")
fi

token="${CONSUMER_TOKEN:-${GH_TOKEN:-}}"
for repo in ${CONSUMER_REPOS:-}; do  # owner/name, separated by spaces or newlines
  dest="$work/repos/${repo##*/}"
  echo "Cloning consumer $repo"
  if [ -n "$token" ]; then
    url="https://x-access-token:${token}@github.com/${repo}.git"
  else
    url="https://github.com/${repo}.git"
  fi
  if git clone --quiet --depth=1 "$url" "$dest" 2>/dev/null; then
    args+=(--consumer "$dest")
  else
    echo "::warning::Could not clone $repo. Does the token have read access to it?"
  fi
done

# --- 3. Run ----------------------------------------------------------------

set +e
blastradius check "$work/base-spec" "$SPEC" ${args[@]+"${args[@]}"} \
  --format markdown --output "$work/report.md" --fail-on "$FAIL_ON"
code=$?
set -e
out exit-code "$code"
if [ "$code" -ge 2 ]; then
  exit "$code"  # bad input: the error is already printed
fi

cat "$work/report.md" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

# --- 4. PR comment ---------------------------------------------------------

if [ "$COMMENT" = "true" ] && [ -n "${PR_NUMBER:-}" ]; then
  { echo "$MARKER"; cat "$work/report.md"; } > "$work/comment.md"
  api="repos/${GITHUB_REPOSITORY}/issues"

  # Update our previous comment instead of adding a new one on every push.
  existing="$(gh api "$api/$PR_NUMBER/comments" --paginate \
    --jq ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | head -n 1 || true)"

  if [ -n "$existing" ]; then
    gh api -X PATCH "$api/comments/$existing" -F "body=@$work/comment.md" >/dev/null \
      && echo "Updated the BlastRadius comment on PR #$PR_NUMBER" \
      || echo "::warning::Could not update the PR comment (missing pull-requests: write?)"
  else
    gh api -X POST "$api/$PR_NUMBER/comments" -F "body=@$work/comment.md" >/dev/null \
      && echo "Commented on PR #$PR_NUMBER" \
      || echo "::warning::Could not comment on the PR (missing pull-requests: write?)"
  fi
fi

exit "$code"
