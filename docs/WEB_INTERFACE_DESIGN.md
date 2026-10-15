# JobPilot browser interface and Azure deployment

Status: Proposed design for user review. No web application has been deployed.

## Goal

Build a private browser interface for https://github.com/megamind294/aijobFiller and deploy it to the existing Ubuntu VM, jobpilot-vm. The user can manage job-search records and generate resumes without a terminal. Work happens when the user requests it; no periodic discovery or background applications are enabled.

## Approach

Recommended: a Flask application using server-rendered HTML and a small amount of JavaScript. Reuse Store, profile validation, public-feed discovery, resume generation, and Excel export. Keep the current CLI working. This avoids a separate Node runtime or Docker on the VM, which currently has 1 GiB RAM.

Alternatives considered: a separate React frontend adds another build and API layer; a hosted static frontend still needs a protected backend on the VM. Neither is necessary for this personal application.

## Screens and interactions

- Dashboard: counts by application status, profile readiness, recent jobs, and links to the main tasks. Empty states explain how to add the first job.
- Jobs: searchable queue with status filters; import JSON/CSV, manually add a job, and open a details page showing requirements, history, resume, and a link to the employer's application page.
- Job actions: prepare a factual resume, download its PDF, and update application status with a required note. Require explicit confirmation for recording submitted/confirmed outcomes; never imply the app submitted a form.
- Profile: edit contact details, summary, skills, experience, projects, education, languages, and saved answers. Support structured JSON editing for nested sections initially. Explain validation errors and require an explicit facts-reviewed checkbox before resume preparation.
- Sources: edit supported Greenhouse/Lever board configuration and title/location filters; discover jobs on demand. No automatic worker is started.
- Export: download the generated applications workbook. Resume downloads are tied to job IDs and restricted to the private data directory.

Use a responsive light interface with dark navy navigation, teal action accents, clear tables/cards, accessible labels, and visible success/error feedback. No imagery or asset generation is needed.

## Boundary of the first version

The browser interface manages data and resumes. Existing Playwright form filling requires an interactive browser and is not exposed as a remote desktop. Employer application links open on the user's device for review and submission. Gmail OAuth, inbox reading, paid AI ranking, continuous discovery, and unattended application submission are not enabled by this deployment. They must not appear as working features in the UI.

## Authentication and security

One personal account with a generated initial password, stored as a password hash on the VM. Store the initial credential in a private local handoff file rather than printing it into logs. Provide a change-password action and invalidate sessions when the password changes.

Require login for every application/data route. Use secure HTTP-only SameSite cookies, a separately generated persistent session secret, CSRF protection for all mutations, login throttling, escaped templates, file-upload limits, and explicit HTTP(S)-URL validation. Do not expose a shell-command endpoint. Do not render imported descriptions as trusted HTML or log passwords, tokens, profile fields, or uploaded file contents.

Use a dedicated service account and the existing /var/lib/jobpilot data directory. Preserve existing files and database records; initialize only missing template files. Keep code read-only to the service. Serialize discovery/resume work and show a busy/error response rather than starting duplicate operations.

## Deployment

Run the web server with Gunicorn on 127.0.0.1:8080 under a hardened systemd service. Use Caddy as an HTTPS reverse proxy. Assign an available Azure-managed DNS label to the existing public IP, so no purchased domain is required. The exact hostname depends on Azure DNS-label availability.

Allow inbound HTTP/HTTPS for the web endpoint; keep SSH limited to the current user's public IP. Bind the Python server to loopback only. Issue and validate a TLS certificate before exposing the login page as ready. If DNS/certificate issuance is blocked, provide a private SSH-tunnel preview while fixing the deployment; do not send login credentials over public HTTP.

Back up existing application data before any schema-affecting operation and retain the existing code revision for rollback. Deploy the reviewed source without uploading local secrets, private SSH keys, Git metadata, or runtime data. The web service may remain running to serve user requests; it performs no scheduled job-search work.

## Validation

Run the repository's existing tests and new web tests for login/logout, CSRF rejection, unauthorized downloads, upload limits, job import validation, preservation of job history, profile validation, resume downloads, status confirmations, and empty states. Use temporary data and mocked public feeds; never submit an employer application during testing.

Inspect the interface in a browser at desktop and narrow widths. Validate the deployed HTTPS login page and authenticated dashboard, verify the Gunicorn port is not publicly exposed, and verify the systemd service is healthy. Report any features requiring profile setup separately from deployment health.

## Acceptance

The user receives a working HTTPS URL, private login instructions, and a running dashboard. They can save a profile, import a test job, generate/download a factual resume, review its history, and download Excel without using a terminal. The existing CLI remains usable, and no scheduled discovery or application submission is enabled.
