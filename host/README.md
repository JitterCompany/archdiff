# host

Serves archdiff pages behind a GitHub login. CI uploads each PR's page with a key that can only write into that repo's folder.

```
https://<domain>/<repo>/pr-<n>/   ->   /srv/archdiff/<repo>/pr-<n>/index.html
```

| Part | Role |
|---|---|
| nginx | TLS, static files, asks oauth2-proxy on every request |
| oauth2-proxy | GitHub login, only members of one org (or team) get in |
| rrsync | the CI key can only write into `/srv/archdiff/<repo>` |
| cron | removes PR pages untouched for 180 days |

Assumes Debian 12 / Ubuntu 22.04 or newer, DNS for the domain pointing at the VPS, and ports 80 and 443 open.

The full walk-through, including the GitHub side, is in [docs/setup.md](../docs/setup.md). The short version, from the repo root:

```sh
scp -r host <vps>:archdiff-host
ssh <vps>
sudo mkdir -p /etc/archdiff
sudo cp archdiff-host/oauth2-proxy.env.example /etc/archdiff/oauth2-proxy.env
sudo nano /etc/archdiff/oauth2-proxy.env      # client id/secret, cookie secret, org
cd archdiff-host && sudo ./setup.sh <domain> <letsencrypt-email>
sudo ./add-repo.sh <repo> archdiff-<repo>.pub  # once per repo
```

`setup.sh` is safe to re-run. It rewrites its own config and keeps the env file, the certificate and the pages.

## Upload test

```sh
mkdir -p /tmp/t && echo hello > /tmp/t/index.html
rsync -a -e "ssh -i archdiff-<repo> -o IdentitiesOnly=yes" /tmp/t/ archdiff-upload@<domain>:pr-0/
```

Any other command with that key is refused.
