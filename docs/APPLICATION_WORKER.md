# Application review and worker integration

`register_applications(app)` and `register_automation(app)` register `/applications`, `/applications/<id>`, `/applications/prepare/<job_id>` (POST), and `/auto-apply`. All use `workspace.current_home/current_store` and the authenticated account. Root authentication and Flask CSRF protection must remain enabled.

`web_applications.approve_and_queue(identifier, approval_hash, action)` is the shared web/API/MCP operation, with `action` equal to `documents` or `form`. It validates current source facts and exact artifact/form hashes, reserves `application:<id>` in the root `CreditLedger`, enforces the shared timezone daily cap, and enqueues an idempotent fill or submit task. Form approval explicitly authorizes one submission attempt.

Documents remain separate immutable PDF/source-JSON versions. Editing a cover letter creates a new package, resets approvals, supersedes the previous package, and releases the old reservation. Profile or posting edits invalidate pending approval. Named lane profiles use `ProfileVault` and invalidate against that named profile rather than the currently selected default. Off and Honest are deterministic; Aggressive is unavailable. No OpenAI requests occur.

## On-demand execution

No scheduler or worker is activated by importing the app. An operator can run one queued task:

```powershell
python -m jobpilot.worker --root C:\private\jobpilot --account owner
```

For a customer, use its existing server account ID. To evaluate explicitly activated lanes first, add `--run-lanes`. To process up to a bounded number of tasks, add `--max-tasks 10`. These commands can make employer submissions when a form has already been explicitly approved or an authorized Automatic lane queues one. Do not run them against real employers while validating deployment.

`--home` is an alias for the private storage root; `--once` is the default. Without `--account`, one pass processes the legacy owner root and existing account folders whose IDs exist in the account database. `--watch` repeats bounded queue passes every five seconds until interrupted; it does not evaluate lanes unless `--run-lanes` is also explicitly supplied. No folder name alone grants an account identity.

The server-private `<root>/application-worker.json` contains `adapters`, keyed by exact origin, and optionally `browser_executable`. The web config `JOBPILOT_APPLICATION_ADAPTERS` should expose the same adapter map for accurate automatic-lane previews. No adapters are configured by default. A map entry requires `origin`, `submit_selector`, `success_selector`, `success_text`, and optional `fields` mappings (`selector`, `kind`, `profile_key`; file mappings use `resume` or `cover_letter`). Use source-supported text/select values and verified boolean checkbox values. Only configure permitted employer integrations whose full behavior has been tested. Install Playwright Chromium separately for the deployment runtime.

`JOBPILOT_APPLICATION_ADAPTERS_FILE` overrides the config-file path. Its JSON shape is `{"adapters": {"https://permitted-employer.example": {"origin": "https://permitted-employer.example", "submit_selector": "...", "success_selector": "...", "success_text": "..."}}}`. Do not use placeholder selectors as a production adapter. `JOBPILOT_ROOT` provides the CLI root default if no `--home`/`--root` is supplied. `PLAYWRIGHT_BROWSERS_PATH` is supported by Playwright for the operator-installed browser location. Adapter config changes require restarting the worker.

## Optional systemd queue consumer

`deploy/jobpilot-application-worker.service` is a runnable example aligned with the web service's `/opt/jobpilot-web/current` and `/var/lib/jobpilot` paths. It consumes approved queued work across existing accounts and starts no lanes by itself. It is an example only: it was not installed, enabled or started during development.

Before enabling it, the operator installs Chromium into the configured path as the service user, installs the trusted adapter config at `/etc/jobpilot-application-adapters.json`, and confirms that its adapters match the web configuration. An empty adapter map is valid and safely pauses unsupported forms. The process must share the web service's Unix account and private storage root.

```sh
sudo install -o root -g root -m 0644 deploy/jobpilot-application-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobpilot-application-worker.service
sudo journalctl -u jobpilot-application-worker.service -f
```

These are operator instructions, not commands executed by this change. To also evaluate already activated lanes periodically, the operator must explicitly add `--run-lanes` to `ExecStart` and reload/restart the unit. Lane settings remain off until the account activates them. An invalid source profile or expired entitlement leaves automatic work paused.

Every browser task acquires the storage root's `heavy-work.lock` before claiming a queue item. OCR uses this same OS-owned lock, so only one browser or OCR process runs across all accounts. A busy lock leaves tasks queued; the next watch pass tries again. The lock is released by the OS after a crash. The service uses SIGINT for a clean shutdown; any interrupted attempted submission remains protected by the durable receipt/uncertainty rules.

Each browser session is headless and fresh, has no saved credentials, and blocks requests outside the configured origin. CAPTCHA, login, embedded forms, missing required answers, unsupported forms, and changed live form values pause for supervised continuation. Review-first never silently falls back to automatic submission. The UI explains the pause and links the employer page; it does not automate CAPTCHA or account login.

The queue uses database leases and unique application/action keys. Expired unattempted form work can be reclaimed; a marked submission attempt is never retried. An expired or interrupted attempted submission remains uncertain until portal verification. A click is insufficient: the worker must observe the configured visible employer receipt on the configured origin before it records SUBMITTED and settles the credit reservation. A late housekeeping failure cannot erase a recorded receipt. Uncertain submissions retain their reservation pending reconciliation; do not blindly refund and retry.

Each worker pass also idempotently settles reservations for already persisted observed SUBMITTED receipts. This recovers a crash between receipt persistence and billing settlement without another employer visit.

## Deliberate scope limits

The default schedule is on demand; there is no silently installed recurring job. Discovery is provided by the source subsystem; lanes evaluate the current account job catalog. Skill coverage is a transparent deterministic keyword measure, not ATS probability. Eligibility and custom required answers come from verified profile values; missing answers require profile correction/new package or supervised browser continuation. Site-specific forms, login and CAPTCHA do not have a universal adapter. No real employer application was sent during development: browser submission verification uses only the localhost demo.

Targeted checks: `test_applications.py`, `test_application_worker.py`, `test_automation.py`, and `test_web_applications.py`. API/MCP transport tests separately verify the same reservation and queue path.
