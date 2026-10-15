# JobPilot master product implementation plan

> **For agentic workers:** Use superpowers:executing-plans for shared integration and superpowers:dispatching-parallel-agents for the three disjoint feature domains. Track tests and progress in docs/MASTER_PROGRESS.md.

**Goal:** Implement the supplied consolidated observations as a working, tested JobPilot product on the existing Python foundation.

**Architecture:** Preserve the current Flask/SQLite/ReportLab application. Add versioned documents, durable application tasks, account-scoped workspaces, separate subscription/credit state, and modular web routes. The supplied documents are the approved specification; native execution was selected earlier and the user has now explicitly instructed proceeding.

**Tech stack:** Python 3.12, Flask, SQLite transactions, ReportLab, local Poppler/Tesseract, Playwright, Google OAuth/API; server-side provider integrations where configured.

**Spec:** docs/jobpilot-master-observations.md (identical to the extensionless copy; GitHub e44e53a).

## Global constraints

- Preserve original files, exact generated versions, current profile/application records and existing owner login.
- Owner bypass is server-authorized; credits are not cryptocurrency.
- Unknown facts, numeric achievements, required answers and work authorization are never invented.
- Document approval and completed-form approval are independent; review-first never silently submits.
- External provider calls require configuration. The user declined local API-key saving; do not create/write a key or implement OpenAI-dependent calls until separately resolved.
- No checkout purchase, mailbox sending, employer submission, automation activation or new paid cloud resource is performed during development.
- UI, API and workers share operations and account scope. Reject stale versions and duplicate charges/submissions.

## Review focus

1. Cross-account data/attachment access: request-scoped home/store and ownership checks must prevent it.
2. Empty/error OCR: keep prior version and profile; test actual scanned input on Linux.
3. Concurrent cap/credit reservation and duplicate provider event: atomic transactions/idempotency.
4. Ambiguous/stale email: never infer submission or overwrite newer outcomes; allow correction.
5. Profile edits after preparation: exact old artifact remains auditable, pending approval invalidated.

## Task 1 — preserve baseline and OCR repair

Files: resume_uploads.py, resume_upload.html, test_resume_ocr.py, deploy/AZURE_WEB.md.
- [x] Reproduce image-only PDF and missing OCR; implement bounded local fallback and Read again.
- [x] Reproduce/fix empty reread and decorative blank pages; share immutable original via hard link.
- [x] Windows full suite: 83 tests, 81 pass, native symlink and real OCR skipped.
- [ ] Linux staged OCR and actual user's current document recovery; rollout after integrated checks.

## Task 2 — request-scoped workspace and account foundations (root)

Create workspace.py: current_home() -> Path, current_store() -> Store, init_workspace(app) sets request-local g from authenticated account, with owner data at existing home. Use request proxies in legacy routes; no global mutable per-request app config. Create accounts.py and account routes: persistent verified-provider identities, session version, server-side owner/customer role, per-account data home, logout/revocation. Provider callbacks validate state/nonce and account association.
- [ ] RED ownership/session/isolation tests; implement; GREEN plus all old auth tests.
- [ ] Preserve existing owner login; add configured Google/email/SMS entry and safe unavailable states.

## Task 3 — profile forms and document vault (profile domain)

Own: web_settings.py profile route only, profile_forms.py, profile_vault.py, profile/vault templates, profile_forms.js, tests/test_profile_forms.py and test_profile_vault.py. Do not edit web.py/base.html/app.css/core.py.
Interfaces: current_home()/current_store() from workspace.py; register_profile_vault(app). Preserve /profile and legacy JSON submissions for compatibility. Named repeatable fields experience_<index>_<field>, projects_..., education_..., answers_...; explicit draft saving and field errors. Profile vault stores immutable snapshots, selectable defaults and original documents; section/style preferences are validated rather than executable content.
- [ ] RED normal-form, incomplete draft, repeated entries, versions/default/rename/delete-protection and stale artifact tests.
- [ ] Implement normal editors, multi-profile selection and baseline resume preview/download.
- [ ] GREEN targeted tests, provide route/assets integration notes.

## Task 4 — discovery and matching (discovery domain)

Own: sources.py, discovery_catalog.py, web_discovery.py, browse templates, discovery.js, tests/test_discovery_catalog.py and test_web_discovery.py. Do not edit web.py/base/app.css/web_settings.py/core.
Interfaces: register_discovery(app), current_home()/current_store(). Add Ashby public feed and registry; metadata table in Store database tracks first_seen/last_seen/published/update/hash separately. Match explanations from profile facts/constraints, not ATS success probability. Routes /browse and /browse/discover (POST), /browse/import-url (POST), /browse/<key> (GET). URL ingestion only tested supported public hosts; bound redirects/private addresses/response/time.
- [ ] RED feed normalization, filter semantics, dedup progress preservation, missing timestamps, untrusted URLs and explanation tests.
- [ ] Implement cards/details/search/filter/save/pass and registry normal forms.
- [ ] GREEN targeted tests, document supported source limits.

## Task 5 — inbox and routing (mail domain)

Own: inbox.py, web_inbox.py, mailbox_provider.py, inbox templates, inbox.js, tests/test_inbox.py and test_web_inbox.py. Do not edit web.py/base/app.css/auth/core.
Interfaces: register_inbox(app), current_home()/current_store(). OAuth callback remains session authenticated and validates one-time state; app needs Google web client config before connect. Existing CLI Gmail unchanged. Routes /inbox, /inbox/sync, /inbox/connect, /inbox/callback, /inbox/disconnect, /inbox/<message>/route, /inbox/<message>/draft. Sync is on demand by default. Never send email; local reply drafts only unless scoped write is authorized.
- [ ] RED dedup, same-company ambiguity, identifier matching, stale status ordering, hostile mail content, relink/manual override and auth/state tests.
- [ ] Implement real configured Gmail connector + two-column inbox, route evidence and correction, draft storage.
- [ ] GREEN targeted tests; no fake live messages or test sync against personal mail.

## Task 6 — documents, review and durable application queue (root)

Create preparation.py, applications.py, worker.py and web_applications.py. Interfaces prepare_application(store, profile, job_id, options)->run_id; get exact snapshot/diff; record approval tied to hashes; queue claim with lease/idempotency; receipt only from observed confirmation. Use current supervised filler with supported local fixtures; external automatic runs require verified configured adapters and explicit activation. Pending drafts are invalidated by source/version changes. Deterministic factual mode works without API. Cover letters remain editable and source-supported.
- [ ] RED snapshot, independent approvals, stale edit, task restart, uncertain submit, duplicate claim, required-answer and receipt tests.
- [ ] Implement persistent workflow and review UI, version downloads, selectable resume/cover settings.
- [ ] GREEN actual demo form and integration tests.

## Task 7 — credits, billing and automation lanes (root)

Create billing.py, web_billing.py, automation.py, automation routes/templates. Decimal catalog uses dated configured PLN/USD rate and 20 PLN per purchased package, not credits. Signed hosted-checkout webhooks grant once; reservations settle/refund atomically. Owner bypass server-side only, no misleading coin balance. Shared cap across lanes, time-zone aware schedule preview, review policy and optimization; inactive until explicit user action. Currency/plan/provider setup needed before real customer checkout.
- [ ] RED duplicate webhook, forged admin, partial refund, bucket/cycle, concurrent cap, disabled lane and DST preview tests.
- [ ] Implement catalog/ledger/settings/dry-run/history; no real purchase or automatic employer run.
- [ ] GREEN targeted tests.

## Task 8 — shell, tracker, research/network and assistant service surface (root)

Own web.py, web_auth.py, web_jobs.py, base/dashboard templates, app.css/app.js and new tracker/research/network/settings templates. Create shared product service and authenticated scoped API tokens. Both future assistant clients consume these services; no alternate permissions/database. Chat UI may expose unavailable configured-provider state but cannot simulate model answers. OpenAI Responses/MCP OAuth implementation waits for credential workflow; document exact remaining integration dependency.
- [ ] RED route/auth/action/search/counters/accessibility, token revocation and exact attachment tests.
- [ ] Build airy white/forest/pastel responsive shell with mobile bottom navigation, drawers, cards, loading/error/empty states, real counters and original branding.
- [ ] Tracker derives actual events; Networking stores user-authored contacts/outreach drafts; Research is authored/public source content rather than invented news.
- [ ] GREEN complete suite, 1440x1000 and 390x844 browser checks, installed-package resources.

## Task 9 — integration, review and delivery

- [ ] Review whole branch in a fresh context, fix Critical/Important once with regressions; retain minors explicitly.
- [ ] Verify Linux OCR/locks/resources, backup private data before schema rollout and keep previous release.
- [ ] Push authorized repository changes and deploy the tested release under existing Azure authorization.
- [ ] Verify public HTTPS, owner login, new routes, actual user's OCR recovery with no automatic draft import; report exact provider-configuration gaps.

Additional delivery gates: real OpenAI calls require the secure key workflow; Gmail/Google/SMS/checkout credentials must be configured for live external operations. A missing credential never justifies a fake success or claiming all reference functionality complete.
