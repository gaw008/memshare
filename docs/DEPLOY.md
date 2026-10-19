# Deploying and updating (systemd + Cloudflare Tunnel)

Reference deployment: one Ubuntu 24.04 server, a dedicated system user with systemd, and public HTTPS provided by a dedicated Cloudflare Tunnel, with no inbound ports opened. The examples use the default user `ubuntu`, the directory `/srv/memhub` and the domain `mem.example.com`; replace them for your environment.

## Prerequisites for the first install

- Log in as `ubuntu` on Ubuntu 24.04 with passwordless `sudo`. Install `python3.12`, `python3.12-venv`, Git, curl and cloudflared. The install step needs access to GitHub and the Python package index; the running service only uses local resources.
- Your code repository (a fork of this one works) has the code you want to deploy, including the `requirements.txt` exported from the lockfile. Create a separate **private**, empty repository (such as `YOUR_GITHUB_USER/memhub-memory`) for memory backups, with no commits. The first-install script will not overwrite or merge a backup repository that already has history.
- The `memhub` system user does not exist yet; `/srv/memhub/app`, `memory` and `state` do not exist yet; the three memhub system units do not exist yet. The script exits if it finds any of them and does not support re-running a half-finished install.
- Before the user is created, an admin places the SSH configuration in `/srv/memhub/.ssh/`. `/srv/memhub` may already exist solely to hold these SSH files; after creating the system user, the script hands this `.ssh` to `memhub`. The system account's shell is nologin, and the scripts use its home through `sudo -H -u memhub`.
- The SSH `config` defines two aliases, `github-memhub` and `github-memhub-memory`, each with `HostName github.com`, `User git`, its own `IdentityFile` and `IdentitiesOnly yes`. The code deploy key needs read access only; the memory backup deploy key needs write access. Private keys are mode `600`, and GitHub's `known_hosts` entry is verified and configured beforehand. The scripts never generate, print or accept unknown SSH host keys.
- cloudflared for `ubuntu` is already authorized to manage tunnels for your domain (for example, an existing `/home/ubuntu/.cloudflared/cert.pem`). Other existing tunnel configurations and services are left untouched.
- Review the root `memhub.prod.toml`: directories, the listen address `127.0.0.1:8796`, the public URL `https://mem.example.com`, OAuth parameters, permission paths and `[deployment]`. The bundled units assume the fixed `/srv/memhub` layout; if you change the layout, change the units too.

Run every deploy script from an admin checkout that `ubuntu` can read, and keep that checkout for later updates. `/srv/memhub` and its app directory stay at mode `700`, so `ubuntu` cannot read or run scripts inside them directly; the scripts read the production config through passwordless sudo and operate the app as `memhub`. The first-install script runs from this admin checkout and takes that checkout's production config explicitly; it installs that config as `/srv/memhub/app/memhub.prod.toml`. Do not clone into `/srv/memhub/app` beforehand, or the first install will refuse by design.

## Order of steps

From the admin checkout that `ubuntu` can read and that contains the code to deploy, run as `ubuntu`:

```bash
bash deploy/install.sh "$PWD/memhub.prod.toml"
sudo -H -u memhub /srv/memhub/app/.venv/bin/memhub --config /srv/memhub/app/memhub.prod.toml set-password
sudo systemctl restart memhub.service
bash deploy/setup-tunnel.sh /srv/memhub/app/memhub.prod.toml
```

`set-password` asks for the OAuth password twice and writes its argon2 hash to `/srv/memhub/state/.env`. The first install creates `.env` with mode `600`, where `STATIC_TOKENS` holds two random tokens for `claude-code` and `gemini-cli`. Until a password is set, OAuth login is refused; the service and static tokens still work. When you need to configure a CLI, read that file privately on the server, and never paste it into chats, logs or code.

install creates the dedicated system user and three `700` directories, clones the code, creates the venv, installs dependencies and the project, initializes the separate memory Git repository, sets the backup remote, enables `memhub.service` and the hourly backup timer, and finally waits for the local `/healthz` to return `200`.

setup-tunnel only creates `memhub.yml` and `cloudflared-memhub.service`, reuses or creates the tunnel of that name, validates the ingress, enables the `ubuntu` user unit and lingering, checks `127.0.0.1:20245/ready`, and finally runs `cloudflared tunnel route dns --overwrite-dns` to route `mem.example.com`. This step really changes that subdomain's DNS; the script exits if a config or unit of the same name already exists. Other tunnel configurations and services are not changed.

## Post-deployment checks

```bash
curl --fail --silent https://mem.example.com/healthz
curl --fail --silent https://mem.example.com/.well-known/oauth-protected-resource/mcp
curl --fail --silent https://mem.example.com/.well-known/oauth-authorization-server
curl --include --silent https://mem.example.com/mcp
sudo systemctl status memhub.service memhub-backup.timer
systemctl --user status cloudflared-memhub.service
sudo -H -u memhub git -C /srv/memhub/memory remote -v
sudo systemctl start memhub-backup.service
sudo journalctl -u memhub-backup.service --no-pager
```

A `/mcp` request without a token should return `401`, with `WWW-Authenticate` pointing at the public protected resource metadata. The issuer, the authorization/token/register/revocation endpoints and the resource URL in the metadata should all use `public_url`. Connect real clients following [CLIENTS.md](CLIENTS.md), check sources and permissions for each, then reboot the VM to confirm both services come back and the backup timer exists. Real app authorization, public reachability, VM reboot recovery and GitHub pushes can only be verified after deployment; local tests cannot prove them.

## Updating

After publishing new code, update the `ubuntu`-readable admin checkout to that version, then run inside it as `ubuntu`:

```bash
bash deploy/update.sh /srv/memhub/app/memhub.prod.toml
```

The update runs `git pull --ff-only`, installs the locked dependencies and the project, refreshes the three systemd units, restarts memhub, and waits for `/healthz` to return `200`. The production config is a tracked file: if it was edited on the server and the remote changed the same file, the pull fails loudly. Resolve the config difference yourself before updating; the script never overwrites your changes. Updates do not touch the tunnel or DNS.

Troubleshooting:

```bash
sudo journalctl -u memhub.service --no-pager
journalctl --user -u cloudflared-memhub.service --no-pager
sudo journalctl -u memhub-backup.service --no-pager
```

Every tool write already produces a Git commit in the memory repository; the backup unit only runs `git push origin main`, hourly. It does not resolve remote divergence, SSH authentication failures or repository conflicts for you. The access log lives in the state path set in the config and contains only time, identity, tool name and paths.

## Offline operation

At runtime memhub downloads nothing and uses no CDN, remote fonts or external APIs. The first install and updates fetch the published code and dependencies through the SSH Git remote and pip; the Cloudflare Tunnel and the hourly GitHub backup also need outbound internet access by design. If your environment forbids all outbound traffic, prepare local code and a wheel repository in advance, and accept that the Tunnel and GitHub backup cannot run — that is incompatible with the public access and backup design described here, so it does not count as a fully air-gapped deployment.
