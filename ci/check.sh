#!/usr/bin/env bash
# Fail early, with the fix, on setup mistakes that otherwise surface halfway as
# ssh or git errors. Reports every problem at once, not just the first.
set -uo pipefail

DOCS="https://github.com/JitterCompany/archdiff/blob/main/docs/setup.md"
fail=0
err() { echo "::error title=archdiff setup::$1"; fail=1; }

case "${EVENT:-}" in
  pull_request | pull_request_target) ;;
  *) err "archdiff runs on pull_request events; this run is '${EVENT:-unknown}'." ;;
esac

[ -n "${SITE:-}" ] || err "Input 'site' is empty. Set it to the host that serves the pages, e.g. archdiff.example.com."
[[ "${NAME:-}" =~ ^[a-z0-9][a-z0-9._-]*$ ]] ||
  err "Input 'name' must be lowercase letters, digits, '.', '_' or '-' and match the name given to add-repo.sh; got '${NAME:-}'."

if [ -z "${KEY:-}" ]; then
  err "Input 'upload-key' is empty. Add secret ARCHDIFF_UPLOAD_KEY to this repo: $DOCS#3-per-repo-upload-key-and-secrets"
elif ! grep -q "PRIVATE KEY" <<<"$KEY"; then
  err "Input 'upload-key' is not a private key. ARCHDIFF_UPLOAD_KEY must hold the key file without .pub."
fi

if [ -z "${KNOWN_HOSTS:-}" ]; then
  err "Input 'known-hosts' is empty. Add secret ARCHDIFF_KNOWN_HOSTS with the output of 'ssh-keyscan ${SITE:-<site>}': $DOCS#3-per-repo-upload-key-and-secrets"
elif [ -n "${SITE:-}" ] && ! grep -q '^|1|' <<<"$KNOWN_HOSTS"; then
  # Hashed entries (|1|...) cannot be matched by name; everything else lists the host names.
  hosts="$(awk '{print $1}' <<<"$KNOWN_HOSTS" | tr ',' '\n' | sed -e 's/^\[//' -e 's/\]:[0-9]*$//')"
  grep -qxF "$SITE" <<<"$hosts" ||
    err "ARCHDIFF_KNOWN_HOSTS has no entry for '$SITE'. Regenerate it with 'ssh-keyscan $SITE'."
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  err "No git checkout. Run actions/checkout (with fetch-depth: 0) before archdiff."
elif [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  err "The checkout is shallow. Set 'fetch-depth: 0' on actions/checkout; archdiff needs the merge base of the PR."
elif ! git cat-file -e "${BASE_SHA:-}^{commit}" 2>/dev/null; then
  err "Base commit '${BASE_SHA:-}' is not in the checkout. Set 'fetch-depth: 0' on actions/checkout."
fi

[ -f archdiff.toml ] ||
  echo "::warning title=archdiff setup::No archdiff.toml in the repo root; using defaults. See the README for the config."

exit "$fail"
