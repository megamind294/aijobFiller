# Run JobPilot on a Hostinger VPS

Hostinger supports Python applications on VPS hosting. Normal Web/Cloud hosting
is not the deployment target for this project. This repository is prepared for
Linux VPS deployment; no server was accessed or provisioned during packaging.

Official support: https://www.hostinger.com/support/which-programming-languages-and-frameworks-are-supported-at-hostinger/

## What can run unattended

The VPS worker discovers/imports jobs, prepares factual resume PDFs and writes
the SQLite/Excel tracker continuously. It does not serve a public website, so
no domain, reverse proxy or inbound HTTP port is required. The Docker image
contains the worker, not a remote browser desktop.

Applications still need the visible-browser CLI. Run that locally, or in a
separately secured remote desktop on your VPS. A headless SSH session cannot
show CAPTCHA challenges or provide the interactive review interface. No remote
desktop or public browser control endpoint is installed by these instructions.

Do not run local and server workers against separately copied databases and assume
they share history. Keep one authoritative data directory. If moving between
machines, stop all JobPilot processes before transferring the complete directory;
do not copy an actively written SQLite database or overwrite newer application
history. Browser profiles and OAuth tokens are sensitive and may not transfer
cleanly between operating systems. The easiest first deployment is a VPS worker
for discovery and a local browser workflow with manually imported job JSON.

## Option A: Docker Compose worker

Prerequisite: an existing Linux VPS with Docker Engine and the Compose plugin.
Do not recreate an existing VPS to install this application. No paid resources
are purchased by this repository.

```bash
git clone https://github.com/megamind294/aijobFiller.git
cd aijobFiller
docker compose build
docker compose run --rm worker init
```

`init` creates a blank private profile and sources in the named volume. Edit them
through a temporary container shell, or copy reviewed files into the volume using
Docker tooling. Never put your filled profile in the Git repository. Example:

```bash
# Stream config into the volume as the unprivileged worker user:
docker compose run --rm -T --entrypoint sh worker -c 'umask 077; cat > /data/profile.json' < /PRIVATE/PATH/profile.json
docker compose run --rm -T --entrypoint sh worker -c 'umask 077; cat > /data/sources.json' < /PRIVATE/PATH/sources.json
docker compose run --rm worker check
docker compose up -d
```

Replace /PRIVATE/PATH with real local paths on the VPS. Keep the example commands
out of unattended scripts until the files exist and have been reviewed.

```bash
docker compose exec worker python -m jobpilot --home /data health
docker compose logs --tail=100 worker
docker compose stop worker
```

Use `docker compose stop worker` to stop this deployment. The Compose restart
policy intentionally restarts process exits, including the CLI `stop` request.
A graceful `docker compose stop` may terminate a long scan after 45 seconds;
SQLite retains committed transactions and imports deduplicate on restart.
Do not use `docker compose down -v`: that deletes the data volume.

## Option B: systemd worker (without Docker)

On an existing Debian/Ubuntu VPS, install Python 3.10+, python3-venv, git and
DejaVu fonts with the system package manager. Create a dedicated `jobpilot` user,
clone the repo to `/opt/aijobFiller`, create its virtualenv, and install dependencies:

```bash
cd /opt/aijobFiller
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Give the service user read/execute access to the code and virtualenv; keep code
owned by your deployment user. Create `/var/lib/jobpilot` owned by `jobpilot`
with mode 0700, then initialize and populate the private configuration:

```bash
sudo -u jobpilot /opt/aijobFiller/.venv/bin/python -m jobpilot --home /var/lib/jobpilot init
sudo -u jobpilot /opt/aijobFiller/.venv/bin/python -m jobpilot --home /var/lib/jobpilot check
```

After filling the profile and company sources, copy `deploy/jobpilot.service` to
`/etc/systemd/system/jobpilot.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jobpilot
sudo systemctl status jobpilot
sudo journalctl -u jobpilot -n 100 --no-pager
```

Stop with `sudo systemctl stop jobpilot`; its Restart=always policy restarts a
normal process exit. Use `systemctl` rather than the CLI stop-file on this deployment.

## Gmail and deployment boundaries

Gmail is optional for discovery and resume preparation. Its browser-based Desktop
OAuth setup is designed for an interactive machine; the worker does not need mail
credentials. Never commit credentials, refresh tokens, filled profiles or browser
cookies. A website/public dashboard would need its own authenticated UI and secure
remote review design; the present CLI is not automatically a web app.

## Verification

Dockerfile/Compose/service files have been reviewed and basic configuration
checked. They have not been deployed to a Hostinger server. See TEST_RESULTS.md
for Python/browser test coverage. Deployment is complete only after the actual
VPS reports a healthy worker and the configured sources have been verified.
