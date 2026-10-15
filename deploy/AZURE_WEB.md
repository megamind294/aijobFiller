# JobPilot Web on Azure

This adds a private browser interface. It does not configure background discovery,
Gmail, paid AI calls, remote desktop access, or employer form submission.

## Layout

- Tested releases: `/opt/jobpilot-web/releases/<release-id>`
- Current release symlink: `/opt/jobpilot-web/current`
- Private persistent data: `/var/lib/jobpilot`
- Root-owned environment: `/etc/jobpilot-web.env`
- Service: `jobpilot-web.service`
- HTTPS proxy: Caddy, forwarding to `127.0.0.1:8080`

Use the existing Azure public-IP resource's DNS label to obtain a hostname.
Allow TCP 80 and 443 in the VM security group for HTTPS/ACME; keep SSH restricted
to the owner's IP. Do not expose port 8080.

## Install a release

Transfer the tested source over host-verified SSH, excluding private data,
credentials, virtual environments, Git metadata, and logs. Record the prior
release and back up `/var/lib/jobpilot` as root before changing anything.

Inside a new versioned release directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
.venv/bin/python -m pip install --no-deps .
sudo .venv/bin/python deploy/bootstrap_web.py --hostname YOUR_AZURE_HOSTNAME
sudo -u jobpilot .venv/bin/python -m jobpilot --home /var/lib/jobpilot init
```

The initializer preserves existing data and credentials. It generates a private
initial credential handoff at `/var/lib/jobpilot/initial-web-login.txt`; retrieve
that file over SSH without printing its contents to shared logs.

Point `current` at the tested release and install `deploy/jobpilot-web.service`
under `/etc/systemd/system/`. Configure Caddy using `Caddyfile.example`, replacing
the hostname, and validate its configuration. Then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now jobpilot-web
sudo systemctl reload caddy
sudo systemctl is-active jobpilot-web caddy
```

Verify certificate issuance and the public HTTPS login before using credentials.
Sign in as `admin` and change the initial password under Account settings; that
revokes all prior sessions. Treat the initial credential file as a secret even
after changing the password.

## Operations

Scanned-resume OCR requires Ubuntu packages `poppler-utils`, `tesseract-ocr`,
and `tesseract-ocr-eng`. Install them with `sudo apt-get install -y poppler-utils
tesseract-ocr tesseract-ocr-eng`. OCR runs locally on demand, one page at a time,
with a 2400-pixel raster limit, one Tesseract thread, a 60-second OCR budget,
and the extraction worker's 256 MiB memory limit. Native text remains preferred.
Up to 10 image-only pages are supported per document; OCR output needs review.

```sh
sudo systemctl status jobpilot-web --no-pager
sudo journalctl -u jobpilot-web -n 50 --no-pager
sudo systemctl restart jobpilot-web
sudo systemctl stop jobpilot-web
sudo systemctl disable jobpilot-web  # prevents automatic start on reboot
```

Stopping the web service stops requests. It does not stop the VM or change SSH
access. The web service waits for user requests; it runs no periodic job tasks.

## Backup and rollback

Stop the web service before backing up the data directory or restoring it.
Backups include private profile records and authentication data; keep them
root-readable only. To roll back code, restore the prior `current` symlink and
restart the service. Preserve `/var/lib/jobpilot` unless a separately verified
database restoration is necessary. The original `/opt/aijobFiller` checkout is
kept intact during the first web rollout.

## First use

Save your real profile, verify it, and add/import jobs. Prepare and review a resume,
then open the employer's application on your own device. JobPilot records a
submitted status only after you explicitly confirm it. A job match is not proof
of work authorization or employer eligibility.
