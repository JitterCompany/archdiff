#!/usr/bin/env bash
# Post the summary as one PR comment, or update the one posted before.
set -uo pipefail

marker='<!-- archdiff -->'
body="$(printf '%s\n%s' "$marker" "$(cat "$RUNNER_TEMP/archdiff-summary.md")")"
out="$RUNNER_TEMP/archdiff-comment.log"

api() { gh api "$@" > "$out" 2>&1; }

if api "repos/$GITHUB_REPOSITORY/issues/$PR/comments" --paginate \
  --jq ".[] | select(.body | startswith(\"$marker\")) | .id"; then
  id="$(head -n1 "$out")"
  if [ -n "$id" ]; then
    api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$id" -f body="$body" && exit 0
  else
    api "repos/$GITHUB_REPOSITORY/issues/$PR/comments" -f body="$body" && exit 0
  fi
fi

cat "$out" >&2
case "$(cat "$out")" in
  *"Resource not accessible by integration"* | *"HTTP 403"*)
    echo "::error title=archdiff comment::The workflow token may not comment. Add 'pull-requests: write' under 'permissions:' in the workflow." ;;
  *)
    echo "::error title=archdiff comment::Posting the PR comment failed. See the log above. The page itself was uploaded." ;;
esac
exit 1
