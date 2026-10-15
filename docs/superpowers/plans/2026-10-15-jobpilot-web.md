# JobPilot Web Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a private HTTPS browser interface for the existing JobPilot application on jobpilot-vm.

**Architecture:** A Flask application reuses the existing Python services and SQLite job history. Server-rendered templates and local static assets provide a responsive interface. Gunicorn serves loopback requests under systemd; Caddy terminates HTTPS at an Azure-managed DNS hostname.

**Tech Stack:** Python 3.12, Flask 3, Flask-WTF 1, Gunicorn 23, SQLite, Jinja, HTML/CSS/JavaScript, Caddy, systemd, unittest.

**Spec:** `docs/WEB_INTERFACE_DESIGN.md` (approved October 15, 2026).

## Global Constraints

- Preserve the current CLI, existing data, profile facts, and application history.
- No scheduled discovery, Gmail access, paid AI ranking, or employer form submission.
- Require login and CSRF protection; never expose a command-execution endpoint.
- Use /var/lib/jobpilot for private data and bind Gunicorn to 127.0.0.1:8080.
- Keep SSH restricted to the user's current public IP; expose only HTTPS and its HTTP redirect.
- No credentials in source control, command output, or application logs.
- Do not claim interactive remote form filling is implemented.

## Review Focus

- Malformed nested profile sections must display validation errors without corrupting saved facts (Task 3).
- Duplicate job imports must preserve submitted/uncertain records and history (Task 2).
- Saved resume paths outside the data directory and symlinks must never expose private server files (Task 2).
- Expired sessions, password changes, and forged POSTs must reject writes (Task 1).
- Missing configuration, empty queues, and concurrent discovery/resume requests must have usable feedback (Tasks 2–4).

## Files and responsibilities

- `jobpilot/web.py`: Flask application factory, dashboard, route registration, error/security response handling.
- `jobpilot/web_auth.py`: login/logout, password rotation, session validation, SQLite login throttling.
- `jobpilot/web_jobs.py`: job queue/details, import/create, status changes, resume and Excel downloads.
- `jobpilot/web_settings.py`: profile editing/validation and on-demand source discovery.
- `jobpilot/web_templates/`: base, login, dashboard, jobs, job detail, profile, sources, password, and error templates.
- `jobpilot/web_static/`: responsive stylesheet and small progressive-enhancement script.
- `tests/test_web_auth.py`, `tests/test_web_jobs.py`, `tests/test_web_settings.py`: Flask test-client coverage using temporary data.
- `requirements-web.txt`: web-only dependencies; preserve CLI dependency behavior.
- `pyproject.toml`: package web templates and static files.
- `deploy/jobpilot-web.service`, `deploy/Caddyfile.example`, `deploy/AZURE_WEB.md`: production service, proxy, and reproducible deployment/rollback instructions.

### Task 1: Authenticated application foundation

**Interfaces:** `create_app(config: dict | None = None) -> Flask` in `web.py`; `init_auth(app: Flask) -> None` and `login_required(view)` in `web_auth.py`. Config uses `JOBPILOT_HOME`, `SECRET_KEY`, `PASSWORD_HASH`, `AUTH_VERSION`, and `TESTING`; production requires nonempty secrets and secure cookies. Load sensitive production config from a root-owned service environment file.

- [ ] Add auth tests: anonymous GET redirects to login; anonymous download does not disclose data; missing/invalid CSRF rejects POST; wrong password rejects login; valid login opens dashboard; logout rejects further access; password rotation invalidates an older client session; repeated failures receive HTTP 429.
- [ ] Run `python -m unittest discover -s tests -p test_web_auth.py -v`; confirm failures before implementing the app.
- [ ] Implement the factory, protected blueprints, session expiry, password hashing/checks with Werkzeug, CSRF, persistent login throttling, and generic error pages. Apply CSP, frame denial, no-sniff, and no-store for private responses. Validate redirect targets as local paths.
- [ ] Create login/password/base templates and include web assets in package data. Set a 5 MiB upload/request limit.
- [ ] Rerun auth tests; confirm they pass, including secure-cookie behavior under HTTPS.
- [ ] Commit this independently tested foundation.

### Task 2: Job workflow and protected downloads

**Interfaces:** `register_jobs(app: Flask) -> None`; reuse `Store(home)`, `Store.add(job)`, `Store.get(id)`, `Store.rows()`, `Store.update(id, status, ...)`, `Store.export()`, `read_jobs(path)`, and `prepare(store, id, profile, use_ai=False)`.

- [ ] Add tests for empty queue, search/status filters, invalid HTTP(S) URLs, invalid import shape, oversized upload, duplicate import preserving SUBMITTED and SUBMISSION_UNCONFIRMED states, explicit confirmation plus required note for status changes, and escaped job descriptions.
- [ ] Add download tests: authorized valid PDF starts with `%PDF`; export is a workbook; unknown job returns 404; absolute external paths, path traversal, and symlink escapes are rejected; unauthenticated requests disclose nothing.
- [ ] Run the new job tests and confirm failure.
- [ ] Implement authenticated job list/details, manual creation, JSON/CSV import through temporary files, status/history actions, resume preparation, PDF download, and export. Validate an entire import before writes. Download PDFs only at the resolved job-specific location under the configured home.
- [ ] Guard discovery and preparation with one shared cross-process action lock. Return a clear busy response when an action is already running. Do not alter locked/submitted resume files.
- [ ] Build job templates with an employer-link action opening a new tab with `noopener`; present it as review/apply on the user's device.
- [ ] Run job and existing core/workflow tests; confirm success without real employer requests.
- [ ] Commit the job workflow.

### Task 3: Profile, sources, and dashboard

**Interfaces:** `register_settings(app: Flask) -> None`; reuse `validate_profile(profile)`, `read_json(path)`, `write_json(path, value)`, and `discover(config)`.

- [ ] Add tests for saving an incomplete draft, requiring explicit fact verification for preparation, malformed nested sections, unknown/invalid source types, missing sources, mocked discovery deduplication, partial feed errors, and lock contention. Assert invalid writes leave prior configuration unchanged.
- [ ] Run settings tests and confirm failure.
- [ ] Implement profile/contact fields plus JSON editors for nested factual sections; validate shape before saving. Allow incomplete drafts marked unverified; only verified valid profiles can prepare resumes.
- [ ] Implement source editing with supported Greenhouse/Lever board tokens and title/location arrays. Execute discovery only on a protected POST with mocked-network tests; show counts and errors. Bound source configuration and request execution so the small VM remains responsive.
- [ ] Implement dashboard statistics/readiness/recent jobs. Counts derive from the store; empty states link to profile setup and import.
- [ ] Run settings, auth, jobs, and existing tests; fix any regressions.
- [ ] Commit the complete functional interface.

### Task 4: Browser QA and production packaging

**Interfaces:** WSGI entry point `jobpilot.web:create_app()`; production dependencies installed from `requirements-web.txt`.

- [ ] Finish the responsive CSS and progressive enhancement: clear focus states, accessible labels, mobile navigation/tables, pending-action feedback, and confirmations that also remain enforced server-side.
- [ ] Start a local test instance with temporary sample data and locally generated test credentials; keep production secrets separate.
- [ ] Use supported browser automation to inspect login, profile, import, queue, resume download, export, and status confirmation at desktop and narrow widths. Check empty/error states and browser console errors.
- [ ] Run `python -m unittest discover -s tests -v`, with Playwright installed for the repository's local-only browser test. Report any unavailable browser validation explicitly.
- [ ] Add production systemd unit: dedicated jobpilot account, loopback binding, 1 Gunicorn worker with 4 threads, bounded timeout, restart-on-failure, private temp, read-only code, writable private data, root-owned secrets, and restrictive umask.
- [ ] Add proxy example and instructions for service start/stop, password change, backups, restore, and rollback. Validate Python compilation, installed wheel assets, and CLI `--help`.
- [ ] Commit and review the final diff for spec coverage and secret leakage before deployment.

### Task 5: Azure rollout and end-to-end verification

**Targets:** existing VM `jobpilot-vm`, resource group `jobpilot-vm_group`, public IP resource `jobpilot-vm-ip`, NSG `jobpilot-vm-nsg`.

- [ ] Recheck remote Git status and private-data inventory. Record the existing revision and create a timestamped backup before installation; do not print personal records.
- [ ] Package tested source excluding Git metadata, virtual environments, runtime data, and credentials; transfer through the established host-verified SSH connection. Install in a versioned release directory and preserve /opt/aijobFiller for rollback.
- [ ] Install web dependencies and initialize only missing private template files as the jobpilot user. Generate login/session secrets securely on the VM, persist a private initial-credential handoff file locally, and never print credentials to command output.
- [ ] Assign an available Azure DNS label to the existing public IP; inspect current networking before adding only the required HTTP/HTTPS rules. Install/configure Caddy and the web systemd unit.
- [ ] Start the service and verify loopback login, service health, Caddy configuration, DNS resolution, certificate issuance, and public HTTPS. Confirm public HTTP redirects to HTTPS and Python port 8080 is not exposed.
- [ ] Log in through the deployed HTTPS site with the handoff credential and verify dashboard and logout. Use a temporary, isolated test data directory for destructive workflow checks; keep the real queue/profile unchanged.
- [ ] If rollout fails, preserve diagnostics without secrets and restore the previous release/configuration rather than leaving an unprotected listener.
- [ ] Deliver the HTTPS URL, private credential-file link, enabled features, any remaining profile setup, and service stop instructions. State that no background discovery or employer submission is enabled.

## Execution recommendation

Native execution in this chat is recommended: the auth, job actions, templates, and deployment share a small set of tightly coupled interfaces. Complete each task's tests before moving forward and request a fresh whole-change review before rollout. Do not push to GitHub or open a PR unless requested; deployment to the user's existing VM is authorized.
