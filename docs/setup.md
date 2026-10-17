# Setup: archdiff on every pull request

The end result: each PR gets one comment with a short architecture summary and a link to the full page. The page sits behind a GitHub login on your own host.

```
PR push -> GitHub Actions: archdiff generates the page -> rsync to the host -> PR comment with the link
                                                                 |
                              reviewer clicks -> GitHub login -> page
```

The tool runs in CI, pinned by a tag. The host only serves files. It never runs or hands out code.

Do the parts in this order. Each part ends with a check.

## 1. Config in the repo

Add `archdiff.toml` to the root of the repo you want to analyse. See the [README](../README.md#config) for the keys. Try it locally before touching CI:

```sh
git clone https://github.com/JitterCompany/archdiff ~/archdiff
cd your-repo
PYTHONPATH=~/archdiff python3 -m archdiff <base-sha> <head-sha> --out /tmp/diff.html --summary /tmp/summary.md
```

Pick a merged PR you know well and check that the page tells its story.

**Check:** the page opens and the headline matches what you know about that PR.

## 2. Host

You need a small VPS with a domain pointing at it, ports 80 and 443 open, Debian 12 or Ubuntu 22.04+.

### 2a. GitHub OAuth app

The login on the host uses a GitHub OAuth app. Create it under the organization that owns the host, so it does not depend on one person's account.

1. Go to `https://github.com/organizations/<org>/settings/applications`, or Organization settings > Developer settings > **OAuth Apps**. You need to be an org owner. Not **GitHub Apps**: that form looks similar but oauth2-proxy expects an OAuth App.
2. **New OAuth App**:
   - Application name: `archdiff`
   - Homepage URL: `https://<domain>`
   - Authorization callback URL (some screens call it *Redirect URI*): `https://<domain>/oauth2/callback`. Exactly this one. Leave wildcard matching off.
   - Leave Device Flow off.
3. **Register**, then **Generate a new client secret**. It is shown once. Keep it for step 2b and nowhere else.

### 2b. Env file on the VPS

```sh
scp -r host <vps>:archdiff-host
ssh <vps>
sudo mkdir -p /etc/archdiff
sudo cp archdiff-host/oauth2-proxy.env.example /etc/archdiff/oauth2-proxy.env
sudo nano /etc/archdiff/oauth2-proxy.env
```

Fill in:

| Key | Value |
|---|---|
| `OAUTH2_PROXY_CLIENT_ID` | from the OAuth app. Not secret. |
| `OAUTH2_PROXY_CLIENT_SECRET` | from the OAuth app. Secret: only in this file. |
| `OAUTH2_PROXY_REDIRECT_URL` | `https://<domain>/oauth2/callback` |
| `OAUTH2_PROXY_COOKIE_SECRET` | `python3 -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'` |
| `OAUTH2_PROXY_GITHUB_ORG` | the org whose members may view the pages |

`OAUTH2_PROXY_GITHUB_ORG` decides who gets in. It does not have to be the org that owns the OAuth app. Use the org that owns the analysed repos, so everyone who reviews those PRs can open the page.

### 2c. Run the setup

```sh
cd archdiff-host && sudo ./setup.sh <domain> <letsencrypt-email>
```

**Check:** `https://<domain>` redirects to GitHub. After login you see an empty directory listing. If you get a 500 after login, see [troubleshooting](#troubleshooting).

### 2d. Org access restrictions

If the org in `OAUTH2_PROXY_GITHUB_ORG` has OAuth app access restrictions (Organization settings > Third-party access), the app cannot see who is a member until the org approves it. You get a 500 after login.

- Org owner: `https://github.com/settings/connections/applications/<client-id>` > Organization access > **Grant** next to the org.
- Not an owner: same page, **Request**. An owner approves it under Organization settings > Third-party access > OAuth application policy.

The grant covers the whole org. Log in again afterwards (private window, or delete the cookie for the domain): membership is only checked at login.

## 3. Per repo: upload key and secrets

On your machine, outside any repo you might commit:

```sh
ssh-keygen -t ed25519 -N "" -C "archdiff-ci-<repo>" -f archdiff-<repo>
scp archdiff-<repo>.pub <vps>:archdiff-host/
ssh -t <vps> 'cd archdiff-host && sudo ./add-repo.sh <repo> archdiff-<repo>.pub'
ssh-keyscan -t ed25519 <domain> > archdiff-known-hosts
ssh-keygen -lf archdiff-known-hosts
```

Compare that fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the VPS. Then add both secrets. `-R` names the repo, so this works from any directory:

```sh
gh secret set ARCHDIFF_UPLOAD_KEY  -R <owner>/<repo> < archdiff-<repo>
gh secret set ARCHDIFF_KNOWN_HOSTS -R <owner>/<repo> < archdiff-known-hosts
rm archdiff-<repo> archdiff-<repo>.pub archdiff-known-hosts
```

**Check:** `gh secret list -R <owner>/<repo>` shows both names.

## 4. Workflow

Add `.github/workflows/archdiff.yml`:

```yaml
name: archdiff

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: archdiff-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  archdiff:
    runs-on: ubuntu-latest
    # Fork PRs get no secrets; skip instead of failing.
    if: github.event.pull_request.head.repo.full_name == github.repository
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      # Only if Cargo.toml has private git dependencies:
      # - uses: webfactory/ssh-agent@v0.10.0
      #   with:
      #     ssh-private-key: ${{ secrets.YOUR_DEPLOY_KEY }}
      - uses: JitterCompany/archdiff@v0.1.1
        with:
          site: <domain>
          name: <repo>
          upload-key: ${{ secrets.ARCHDIFF_UPLOAD_KEY }}
          known-hosts: ${{ secrets.ARCHDIFF_KNOWN_HOSTS }}
```

`fetch-depth: 0` is needed: archdiff compares the PR head with its merge base.

Open a PR that adds this file. A PR that adds a workflow also runs it, so it tests itself.

**Check:** the job is green and the PR has a comment starting with "Architecture diff". On later pushes the same comment is updated.

If your repo has a changelog check on every PR, this PR has nothing user-facing to log. Use whatever skip label that check supports.

## Troubleshooting

The action starts with a **Check setup** step. It reports missing or malformed inputs (empty secrets, a `.pub` file instead of the private key, a known_hosts without the site, a shallow checkout) with the fix, before anything is generated. Upload and comment failures also end with a hint. The table covers the rest, and what those errors look like on versions before v0.1.1.

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to resolve action JitterCompany/archdiff, repository not found` | The action repo is private, or the tag in `uses:` does not exist | The repo must be public (actions are not shared across orgs otherwise). Check the tag with `git ls-remote --tags https://github.com/JitterCompany/archdiff` |
| `Host key verification failed` in the Upload step | `ARCHDIFF_KNOWN_HOSTS` is missing or empty. In the log, `KNOWN_HOSTS:` is blank instead of `***` | Add the secret (step 3) and re-run the job |
| `Permission denied (publickey)` in the Upload step | Wrong key, or `add-repo.sh` used a different `<repo>` than `name:` in the workflow | Run the upload test from [host/README.md](../host/README.md) with the same key |
| 500 right after the GitHub login | oauth2-proxy log says `Missing Organization` | Org access restrictions: grant the app (step 2d). Or you are not a member of `OAUTH2_PROXY_GITHUB_ORG` |
| Error page at GitHub during login | Callback URL in the OAuth app does not match `OAUTH2_PROXY_REDIRECT_URL` exactly | Make them identical |
| After login you land on the listing, not the PR page | oauth2-proxy refused the redirect (`domain / port not in whitelist`) | Re-run the current `setup.sh`; it whitelists the domain |
| Comment says a git dependency is "not in local cargo cache" | The job cannot clone that dependency | Give the job SSH access (`webfactory/ssh-agent`) for private dependencies |

Logs on the host:

```sh
sudo journalctl -u oauth2-proxy -n 40 --no-pager
sudo tail -n 20 /var/log/nginx/error.log
```
