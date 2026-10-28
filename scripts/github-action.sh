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
#   FAIL_ON_CLONE_ERROR, BASE_REF, PR_NUMBER, HEAD_REPO, GITHUB_REPOSITORY, GH_TOKEN
#   GIT_HOST (tests only; defaults to https://github.com)
#
# Exit codes: 0 OK or nothing to compare, 1 the fail-on condition was met (or the
# spec was deleted), 2 the check couldn't run (bad input, fetch or clone failure).
set -euo pipefail

MARKER='<!-- blastradius-report -->'
GIT_HOST="${GIT_HOST:-https://github.com}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

out() { echo "$1=$2" >> "${GITHUB_OUTPUT:-/dev/null}"; }
trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; echo "${s%"${s##*[![:space:]]}"}"; }
finish() { out exit-code "$1"; exit "$1"; }
fail() { echo "::error::$1"; finish 2; }

: "${SPEC:?the 'spec' input is required}"
FAIL_ON="${FAIL_ON:-impacted}"
COMMENT="${COMMENT:-true}"
FAIL_ON_CLONE_ERROR="${FAIL_ON_CLONE_ERROR:-true}"

# --- 1. The base version of the spec ---------------------------------------

if [ -z "${BASE_REF:-}" ]; then
  echo "::notice::Not a pull request, so there's no base version to compare against. Skipping."
  finish 0
fi

# A failed fetch must not look like "the spec is new": that would pass the check.
if ! git fetch --no-tags --depth=1 origin "$BASE_REF" >/dev/null 2>&1 \
  && ! git rev-parse --verify --quiet "origin/$BASE_REF" >/dev/null; then
  fail "Couldn't fetch the base branch '$BASE_REF'. Use actions/checkout before this action."
fi

if ! git cat-file -e "origin/$BASE_REF:$SPEC" 2>/dev/null; then
  echo "::notice::$SPEC doesn't exist on $BASE_REF yet (new API). Nothing to compare."
  finish 0
fi
git show "origin/$BASE_REF:$SPEC" > "$work/base-spec"

if [ ! -f "$SPEC" ]; then
  echo "::error::$SPEC was deleted in this pull request. Every consumer of it will break."
  finish 1
fi

if cmp -s "$work/base-spec" "$SPEC"; then
  echo "::notice::$SPEC is unchanged in this pull request. Skipping."
  finish 0
fi

# --- 2. Consumers ----------------------------------------------------------

args=()
while IFS= read -r line; do
  line="$(trim "$line")"
  if [ -n "$line" ]; then
    [ -d "$line" ] || fail "Consumer folder '$line' doesn't exist."
    args+=(--consumer "$line")
  fi
done <<< "${CONSUMERS:-}"

if [ -n "$(trim "${CONSUMERS_DIR:-}")" ]; then
  dir="$(trim "$CONSUMERS_DIR")"
  [ -d "$dir" ] || fail "Consumers folder '$dir' doesn't exist."
  args+=(--consumers-dir "$dir")
fi

# The token reaches git through a credential helper that reads it from the
# environment, so it never appears in a URL, a command line or git's config.
export BLASTRADIUS_GIT_TOKEN="${CONSUMER_TOKEN:-${GH_TOKEN:-}}"
if [ -n "$BLASTRADIUS_GIT_TOKEN" ]; then
  echo "::add-mask::$BLASTRADIUS_GIT_TOKEN"
fi
# shellcheck disable=SC2016  # expanded by git's shell, not this one
helper='!f() { echo username=x-access-token; echo "password=$BLASTRADIUS_GIT_TOKEN"; }; f'

for repo in ${CONSUMER_REPOS:-}; do  # owner/name, separated by spaces or newlines
  dest="$work/repos/${repo##*/}"
  echo "Cloning consumer $repo"
  if git -c credential.helper= -c "credential.helper=$helper" \
    clone --quiet --depth=1 "$GIT_HOST/$repo.git" "$dest" 2>"$work/clone-error"; then
    args+=(--consumer "$dest")
  elif [ "$FAIL_ON_CLONE_ERROR" = "true" ]; then
    # Skipping a consumer silently could hide exactly the breakage we're looking for.
    fail "Couldn't clone $repo: $(head -n 1 "$work/clone-error"). Check that consumer-token can read it, or set fail-on-clone-error: false."
  else
    echo "::warning::Couldn't clone $repo; it was left out of the analysis."
  fi
done

# --- 3. Run ----------------------------------------------------------------

set +e
blastradius check "$work/base-spec" "$SPEC" ${args[@]+"${args[@]}"} \
  --format markdown --output "$work/report.md" --fail-on "$FAIL_ON"
code=$?
set -e
if [ "$code" -ge 2 ]; then
  finish "$code"  # bad input: blastradius already printed the error
fi

cat "$work/report.md" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

# --- 4. PR comment ---------------------------------------------------------

if [ "$COMMENT" = "true" ] && [ -n "${PR_NUMBER:-}" ]; then
  if [ -n "${HEAD_REPO:-}" ] && [ "$HEAD_REPO" != "${GITHUB_REPOSITORY:-}" ]; then
    # Pull requests from forks get a read-only token, so commenting would fail.
    echo "::notice::Pull request from a fork: the report is in the job summary instead of a comment."
  else
    { echo "$MARKER"; cat "$work/report.md"; } > "$work/comment.md"
    api="repos/${GITHUB_REPOSITORY}/issues"

    # Update our previous comment instead of adding a new one on every push.
    if ! existing="$(gh api "$api/$PR_NUMBER/comments" --paginate \
      --jq ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | head -n 1)"; then
      existing=""
    fi

    if [ -n "$existing" ]; then
      gh api -X PATCH "$api/comments/$existing" -F "body=@$work/comment.md" >/dev/null \
        && echo "Updated the BlastRadius comment on PR #$PR_NUMBER" \
        || echo "::warning::Could not update the PR comment (does the job have pull-requests: write?)"
    else
      gh api -X POST "$api/$PR_NUMBER/comments" -F "body=@$work/comment.md" >/dev/null \
        && echo "Commented on PR #$PR_NUMBER" \
        || echo "::warning::Could not comment on the PR (does the job have pull-requests: write?)"
    fi
  fi
fi

finish "$code"
