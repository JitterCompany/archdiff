# archdiff-host

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

Assumes Debian 12 / Ubuntu 22.04 or newer, and DNS for the domain pointing at the VPS.

## Setup

1. Create a GitHub OAuth app (Settings > Developer settings > OAuth Apps). Callback URL: `https://<domain>/oauth2/callback`.
2. Copy this folder to the VPS and create the env file:
   ```sh
   scp -r archdiff-host <vps>:
   ssh <vps>
   sudo mkdir -p /etc/archdiff
   sudo cp archdiff-host/oauth2-proxy.env.example /etc/archdiff/oauth2-proxy.env
   sudo nano /etc/archdiff/oauth2-proxy.env     # client id/secret, cookie secret, org
   ```
3. Run the setup:
   ```sh
   cd archdiff-host && sudo ./setup.sh <domain> <letsencrypt-email>
   ```
4. Open `https://<domain>`. You should get the GitHub login, then an empty listing.

## Add a repo

On your machine, make a key for that repo's CI:

```sh
ssh-keygen -t ed25519 -N "" -C "archdiff-ci-<repo>" -f archdiff-<repo>
```

On the VPS, allow it to upload into `/srv/archdiff/<repo>`:

```sh
sudo ./add-repo.sh <repo> archdiff-<repo>.pub
```

In the GitHub repo, add two secrets:

- `ARCHDIFF_UPLOAD_KEY`: the private key file `archdiff-<repo>`
- `ARCHDIFF_KNOWN_HOSTS`: the output of `ssh-keyscan <domain>`. Compare the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the VPS.

Then delete the local key files. The workflow uses the `JitterCompany/archdiff` action with `name: <repo>`.

## Upload test

```sh
mkdir -p /tmp/t && echo hello > /tmp/t/index.html
rsync -a -e "ssh -i archdiff-<repo> -o IdentitiesOnly=yes" /tmp/t/ archdiff-upload@<domain>:pr-0/
```

Any other command with that key is refused.
