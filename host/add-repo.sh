#!/usr/bin/env bash
# Give one repo's CI an upload key that can only rsync into /srv/archdiff/<repo>.
#   sudo ./add-repo.sh dashboard ci-dashboard.pub
set -euo pipefail

REPO="${1:?usage: add-repo.sh <repo> <public key file>}"
KEY="${2:?usage: add-repo.sh <repo> <public key file>}"
[[ "$REPO" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { echo "repo name: lowercase letters, digits, . _ -" >&2; exit 1; }

install -d -o archdiff-upload -g www-data -m 2755 "/srv/archdiff/$REPO"
auth=/home/archdiff-upload/.ssh/authorized_keys
line="command=\"rrsync -wo /srv/archdiff/$REPO\",restrict $(cat "$KEY")"
grep -vF "/srv/archdiff/$REPO\"" "$auth" > "$auth.new" || true
echo "$line" >> "$auth.new"
mv "$auth.new" "$auth"
chown archdiff-upload "$auth"
chmod 600 "$auth"
echo "Upload key for $REPO installed. Uploads land in /srv/archdiff/$REPO."
