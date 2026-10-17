#!/usr/bin/env bash
# Set up the archdiff page host on a Debian/Ubuntu VPS. Run as root, from this folder:
#   sudo ./setup.sh archdiff.example.com admin@example.com
# Safe to re-run: it overwrites its own config files and keeps the uploaded pages.
set -euo pipefail

DOMAIN="${1:?usage: setup.sh <domain> <letsencrypt-email>}"
EMAIL="${2:?usage: setup.sh <domain> <letsencrypt-email>}"
OAUTH2_PROXY_VERSION="v7.15.5"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f /etc/archdiff/oauth2-proxy.env ] || {
  echo "Create /etc/archdiff/oauth2-proxy.env first (see oauth2-proxy.env.example)." >&2
  exit 1
}

apt-get update -q
apt-get install -y -q nginx certbot python3-certbot-nginx rsync curl

# Pages live in /srv/archdiff/<repo>/pr-<n>/, written only by the upload user.
id archdiff-upload >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh archdiff-upload
install -d -o archdiff-upload -g www-data -m 2755 /srv/archdiff
install -d -o archdiff-upload -m 700 /home/archdiff-upload/.ssh
touch /home/archdiff-upload/.ssh/authorized_keys
chown archdiff-upload /home/archdiff-upload/.ssh/authorized_keys
chmod 600 /home/archdiff-upload/.ssh/authorized_keys
command -v rrsync >/dev/null || { echo "rrsync not found (needs rsync >= 3.2.4)" >&2; exit 1; }

# oauth2-proxy: pinned release, checksum verified.
arch="$(dpkg --print-architecture)"
case "$arch" in amd64|arm64) ;; *) echo "unsupported architecture $arch" >&2; exit 1 ;; esac
if [ "$(/usr/local/bin/oauth2-proxy --version 2>/dev/null | grep -o 'v[0-9.]*' | head -1)" != "$OAUTH2_PROXY_VERSION" ]; then
  tmp="$(mktemp -d)"
  name="oauth2-proxy-${OAUTH2_PROXY_VERSION}.linux-${arch}"
  base="https://github.com/oauth2-proxy/oauth2-proxy/releases/download/${OAUTH2_PROXY_VERSION}"
  curl -fsSL -o "$tmp/$name.tar.gz" "$base/$name.tar.gz"
  curl -fsSL -o "$tmp/sum.txt" "$base/$name.tar.gz-sha256sum.txt"
  echo "$(awk '{print $1}' "$tmp/sum.txt")  $tmp/$name.tar.gz" | sha256sum -c -
  tar -xzf "$tmp/$name.tar.gz" -C "$tmp"
  install -m 755 "$tmp/$name/oauth2-proxy" /usr/local/bin/oauth2-proxy
  rm -r "$tmp"
fi
id oauth2-proxy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin oauth2-proxy
chmod 640 /etc/archdiff/oauth2-proxy.env
chgrp oauth2-proxy /etc/archdiff/oauth2-proxy.env
# The domain must be whitelisted, or the redirect back to the requested page after login is refused.
sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/oauth2-proxy.service" > /etc/systemd/system/oauth2-proxy.service
chmod 644 /etc/systemd/system/oauth2-proxy.service
systemctl daemon-reload
systemctl enable --now oauth2-proxy
systemctl restart oauth2-proxy

# nginx: TLS from certbot (webroot, so renewals work while nginx runs),
# every request behind oauth2-proxy.
mkdir -p /var/www/html
rm -f /etc/nginx/sites-enabled/default
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  # First run: HTTP only, until the certificate exists.
  cat > /etc/nginx/sites-available/archdiff <<CONF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
}
CONF
  ln -sf /etc/nginx/sites-available/archdiff /etc/nginx/sites-enabled/archdiff
  systemctl enable --now nginx
  systemctl reload nginx
  certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos -m "$EMAIL" -d "$DOMAIN" \
    --deploy-hook "systemctl reload nginx"
fi
sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/nginx-archdiff.conf" > /etc/nginx/sites-available/archdiff
ln -sf /etc/nginx/sites-available/archdiff /etc/nginx/sites-enabled/archdiff
nginx -t
systemctl enable --now nginx
systemctl reload nginx

# Pages of PRs nobody looked at for half a year go away.
install -m 644 "$HERE/archdiff-prune.cron" /etc/cron.d/archdiff-prune

echo "Done. Add an upload key per repo with: ./add-repo.sh <repo> <public key file>"
