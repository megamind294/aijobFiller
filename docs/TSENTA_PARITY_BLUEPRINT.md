# JobPilot product blueprint

Date: October 15, 2026. Status: proposed architecture for review; not a claim that the features below are implemented.

## Goal and evidence

Build a personal job-search platform on the existing Azure deployment using the user's detailed Tsenta walkthrough as the functional reference. The user explicitly included recruiter email routing. Preserve their earlier on-demand preference until they choose a schedule. Retain existing account, application records, uploaded resumes, and history through versioned upgrades.

The public pages were read. Signed-in Tsenta content remains inaccessible to the browser tool despite a visible Always allow setting. Dashboard details in this document therefore come from the user's supplied specification, not independent interaction testing. Treat advertised coverage and scale as claims, not demonstrated capacity.

Public references:

- [Product overview](https://tsenta.com/): discovery, preparation, application handling, and tracking.
- [Review before submit](https://tsenta.com/changelog): editable approval checkpoint before sending.
- [Feature disclosure](https://tsenta.com/ai-disclosure): advertised matching, document changes, automated forms, reply routing, and platform coverage.
- [Mobile](https://tsenta.com/mobile): mobile review/apply flow and multiple profiles.
- [Extension listing](https://chromewebstore.google.com/detail/tsenta/momaobilceffibifldnhndcehhdjgaob): add detected jobs to a cloud queue.
- [MCP](https://tsenta.com/mcp): authenticated agent access.

## Current foundation and gaps

Existing: private authenticated Flask UI, SQLite queue and event history, Greenhouse/Lever discovery for selected company boards, JSON/CSV import, factual PDF generation, original resume storage/download, conservative draft prefilling, manual status confirmation, Excel export. The CLI also contains supervised Playwright filling, narrow Gmail OTP matching, and email action suggestions. Those CLI features are not yet usable as cloud browser workflows or a web inbox.

Immediate defect: the user's 1-page PDF contains one image and no selectable text. The current release saved the original but cannot OCR it. The approved repair adds local OCR and reprocessing of the existing file; review/import remains explicit.

## Product surfaces and acceptance criteria

| Surface | Intended behavior | Proof required before calling it complete |
|---|---|---|
| Dashboard | Search, next actions, job matches, real pipeline counts and recent activity | Every figure derives from persisted records; an empty account shows an honest empty state |
| Browse Jobs | Role/location/salary/work-mode filters, explanation of fit, save/skip, paste URL | Supported feeds return normalized jobs; duplicates preserve progress; unavailable data is marked unknown |
| Applications | Queue plus filters for in-flight, needs review, failed, skipped; bulk preparation; detail/review panel | Actions operate on selected records, recover after restart, and never submit twice |
| Preparation | Versioned resume and cover-letter drafts, original-versus-draft comparison, editable answers, approve/send | Every accepted draft has a version/hash; edits invalidate approval; generated assertions trace to saved facts |
| Application execution | Supported ATS adapters, document upload, custom questions, human handoff, confirmation receipt | Real test forms prove fields and receipt capture; a click or timeout never becomes a successful submission |
| Inbox | Thread list and reader, application links, intent/status suggestions, correction controls, reply drafts | Repeated sync is idempotent; ambiguous mail stays unmatched; status history cites message evidence |
| Tracker | Funnel, per-stage records, upcoming interviews, filters/export | Counts have defined denominators; no invented recruiter views or applicant position |
| Profile & vault | Resume OCR/text review; normal personal/work/education/project forms; preferences and work authorization; document versions | Saving drafts preserves incomplete entries; no inference of work rights; original download stays byte-identical |
| Settings | Account, email connect/disconnect, sync mode, integration health, optional API tokens | Secrets never appear in logs/exports; revocation takes effect; failures have actionable messages |

Navigation follows these seven surfaces. Keep JobPilot's own identity and accessible responsive components rather than reusing Tsenta logos, badges, testimonials, or backing claims.

## Recommended architecture

Extend the existing Python application in modules, retaining SQLite for this single-user VM. Add explicit migrations and transactional repositories for document versions, preferences, application runs, approval records, inbox messages, routes, and sync cursors. Keep existing jobs/events compatible. Avoid an immediate framework/database rewrite: it would delay the user's broken onboarding without supplying job or email access.

Move slow preparation, inbox sync, and browser runs into a durable SQLite-backed task queue with leases, retry limits, cancellation, and observable progress. Begin with one resource-heavy task at a time. A separate worker is started on demand; recurring discovery or mail polling is off by default. Rendering pages never starts a sync. Restart recovery distinguishes retryable preparation from uncertain external submission.

The web app exposes authenticated task creation/status and reviewed results. Future extension/mobile/MCP clients use versioned APIs over the same records, not alternate application engines. Browser sessions and OAuth tokens are private service data, separate from exported records and source control.

Alternatives considered: (1) extending the current modular app is the recommended shortest route to useful tested workflows; (2) a new frontend plus API can improve client reuse but adds migration/build/deployment work before functionality; (3) distributed cloud services can support larger concurrency but require new costs and operational scope. Reassess only after measuring a real need.

## Resume intake and profile

Use native PDF text first; OCR image-only pages locally with Poppler/Tesseract. Preserve the original, extracted text, extraction method, document version, and source attribution. Bound uploads/pages/raster size/process memory/wall time. Never execute PDF attachments or macros. Reading again creates a new extraction version and invalidates stale import forms. Failed parsing retains the current version.

Next replace JSON-first history/education/project editors with repeatable forms. Add field-level errors and saved-answer/work-preference forms. Resume suggestions stay unverified. Parsing cannot reliably infer structured history from every layout: show source text alongside incomplete draft fields for correction. Imported data must not silently overwrite verified history.

## Discovery and matching

Use existing public Greenhouse/Lever adapters, then add Ashby and other explicitly tested public sources. URL ingestion fetches only supported public job origins initially; guard redirects/private addresses, size, timeout, and content types. Unsupported URLs still allow manual description entry. Do not promise that every LinkedIn URL can be fetched.

Define matching from saved skills, role interests, locations, work mode, and explicit authorization answers. Show why a role matched and which constraints are unknown. A displayed percentage must name its actual scoring method; it is not a probability of being hired. Continuous monitoring and broad company coverage require a configured source catalog and chosen run schedule, not an animated ticker.

## Preparation, review, and execution

Keep deterministic factual ordering available without paid AI. An optional configured model may draft cover letters, answers, and edits from verified facts, with provenance and a visible diff. Preserve dates, employers, credentials, and numeric achievements; invented metrics in the reference mockup are not requirements to fabricate facts. The user reviews material changes and answers before sending.

Build a worker state machine: queued -> preparing -> needs_review -> approved -> filling -> needs_user or submitting -> submitted/unconfirmed/failed. Approval binds the exact job URL, profile/document versions, and answers. Retain a receipt containing submitted values, document hashes/versions, timestamp, final URL, and observed confirmation evidence. Confirmation uncertainty blocks automatic retries. Broad ATS coverage is an adapter roadmap, not something the current generic filler already proves.

## Mail routing — first-class subsystem

Start with Gmail OAuth because a read-only Gmail client already exists in the CLI; add Microsoft/IMAP through separate provider adapters later. A connected ChatGPT Gmail app does not authorize the deployed server. The Azure app needs its own configured OAuth web client, exact HTTPS callback, and user consent. Never ask for a mailbox password in chat. Use OAuth state validation, protected callbacks, private encrypted token storage, and a disconnect/revoke path.

Initial default: the user clicks Sync inbox. Record sync time, progress, errors, and cursor. Query bounded relevant candidate messages, retrieve only candidates needed for routing, deduplicate by provider/account/message ID, and persist linked thread data privately. Incremental history cursors need a bounded backfill recovery when expired. Mail bodies are untrusted display content: escape/sanitize them, disable remote image loads, and do not execute instructions from emails.

Route using application/ATS identifiers, stable thread references, recipient, employer/recruiter domain, role title, and application chronology together. A domain alone cannot distinguish two roles at the same company. Auto-link only unambiguous matches with an explanation; otherwise show candidate applications and let the user choose. Allow undo/relink/ignore and retain an audit event. Never turn a newsletter/job alert into an application submission.

Classify acknowledgment, interview/scheduling request, rejection, offer, and other reply. Update status only when routing and classification meet explicit evidence rules; otherwise offer a reviewed suggestion. A message asking for interview availability is not a scheduled calendar event. Old messages cannot overwrite a newer terminal status. Preserve manual corrections and show which email caused each change. Replied counters can derive from linked messages; Viewed remains unavailable unless a real provider supplies that signal.

Reply drafting is separate from routing. First provide editable local drafts from the selected thread and saved profile, without automatic sending. Saving into Gmail's Drafts folder needs an additional scoped provider write permission and explicit setup. No automatic recruiter email is sent. OTP use remains tied to the exact active browser run, sender, recipient, time, and origin; codes are not stored in the tracker.

## Secondary interfaces and commercial features

- Chrome/Brave extension: job detection and add-to-queue first, then tested form assistance. Use minimal site permissions, revocable tokens, and origin-bound APIs. Mark unsupported forms honestly.
- Mobile: responsive/PWA workflow first; native iOS/Android apps and swipe review share the same API later. Swiping alone must not accidentally send an unreviewed application.
- MCP/CLI: expose authorized discovery/preparation/queue/review/status tools, with revocable scopes and confirmation-bound submission. Do not expose a public arbitrary-command tool on the VM.
- Messaging: WhatsApp/iMessage needs provider setup, verified account association, message costs, and opt-in notifications. These are separate integrations, not chat-shaped demo buttons.
- Public marketing: truthful product screenshots, signup/onboarding, FAQ, and actual activity summaries. No fictional crawler counts, speed comparisons, YC badge, applicant rank, or testimonials.
- Billing: keep the personal deployment free of subscription charging. If the user opens a public service, separately implement accounts, payment provider webhooks, receipts, cancellation, and a credit ledger that charges only confirmed successful submissions. Do not charge from a button click or duplicate webhook.

## Delivery sequence

1. Finish and deploy OCR; verify recovery on the user's actual uploaded PDF while leaving their profile unchanged.
2. Profile forms, preferences, resume vault, and dashboard/navigation overhaul.
3. Inbox shell, routing/classification repository with realistic fixtures, Gmail OAuth connect/sync/disconnect; live sync requires the user's OAuth setup.
4. Browse Jobs filters/URL ingestion and explainable matching, backed by real supported sources.
5. Versioned preparation/review with factual diffs, cover-letter/answer editing, and optional configured AI.
6. Durable execution worker, first tested ATS adapters, reviewed submission and evidence receipts.
7. Tracker analytics and integrations; extension/MCP/mobile/messaging and public billing each receive their own tested integration release.

Each delivery is a functional slice with acceptance checks and rollback, not a collection of non-working pages. This is too large for one implementation plan; review the architecture, then plan one slice at a time. No paid model, new Azure service, mail schedule, or employer application is triggered merely by this blueprint.

## Verification and release criteria

Unit/integration tests cover state transitions, idempotency, migrations, stale approvals, data preservation, OAuth errors, ambiguous matching, status ordering, hostile HTML/email text, retries, and real receipt evidence. Browser tests cover desktop/mobile forms, actionable errors, long-running task progress, download, review, and account/session behavior. Linux staging exercises actual OCR/process limits. Preserve a private data backup before schema migration; retain a previous release for rollback. Measure memory before enabling browser concurrency on the existing 841 MiB VM.

No claim of full Tsenta feature parity until each feature's integration and user-visible outcome has been verified. Missing provider credentials are disclosed as configuration requirements rather than simulated success.
