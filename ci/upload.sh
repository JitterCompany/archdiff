#!/usr/bin/env bash
# rsync the page to the host; on failure, say what is probably wrong.
set -uo pipefail

dir="$RUNNER_TEMP/archdiff-upload"
mkdir -p "$dir"
printf '%s\n' "$KEY" > "$dir/key"
chmod 600 "$dir/key"
printf '%s\n' "$KNOWN_HOSTS" > "$dir/known_hosts"

rsync -a --delete \
  -e "ssh -i $dir/key -o IdentitiesOnly=yes -o UserKnownHostsFile=$dir/known_hosts -o ConnectTimeout=20" \
  "$RUNNER_TEMP/archdiff/" "$UPLOAD_USER@$SITE:pr-$PR/" 2> "$dir/stderr"
rc=$?
rm -f "$dir/key"
cat "$dir/stderr" >&2
[ "$rc" -eq 0 ] && exit 0

log="$(cat "$dir/stderr")"
case "$log" in
  *"Host key verification failed"* | *"REMOTE HOST IDENTIFICATION HAS CHANGED"*)
    hint="The host key of $SITE does not match ARCHDIFF_KNOWN_HOSTS. If the server was reinstalled, refresh the secret with 'ssh-keyscan $SITE' after checking the fingerprint on the server." ;;
  *"Permission denied"*)
    hint="$SITE refused ARCHDIFF_UPLOAD_KEY. Check that add-repo.sh was run with name '$NAME' and the .pub of this key." ;;
  *"Could not resolve hostname"* | *"Name or service not known"*)
    hint="Cannot resolve '$SITE'. Check input 'site' and its DNS record." ;;
  *"Connection refused"* | *"timed out"* | *"No route to host"*)
    hint="Cannot reach $SITE on port 22. Is the host up and the firewall open for GitHub's runners?" ;;
  *"rrsync"* | *"not allowed"* | *"denied"*)
    hint="$SITE refused the transfer. The upload key may be restricted to another directory than '$NAME'." ;;
  *)
    hint="Upload to $SITE failed (rsync exit $rc). See the log above." ;;
esac
echo "::error title=archdiff upload::$hint"
exit "$rc"
