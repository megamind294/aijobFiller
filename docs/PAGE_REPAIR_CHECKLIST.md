# JobPilot page repair checklist

User resumed work on 16 October 2026: review every page, including appearance and real behavior. The master observations remain the target. Preserve the Python/Flask/SQLite architecture, account isolation and owner data.

A page passes only when its primary workflow, invalid input, empty/error states, persistence, permissions and desktop/mobile rendering have been checked. A successful GET request is not workflow proof. External-service blockers remain open. Verification does not include live employer submissions or real payments.

## Delivery order

1. **Sign-in and registration — deployed and verified (4addad5).** Shared responsive design, clear customer/owner entry, password visibility, validation, session redirects, account creation and payment gate. Google/OTP delivery requires provider credentials.
2. **Billing and access — pending.** Test checkout, webhook, plan/credit state, cancellation, owner bypass and isolation. Clearly distinguish test mode from real payments.
3. **Profile and resume — pending.** PDF/DOCX upload, OCR, recovered text, editable draft, work/education import, verification, previews and downloads.
4. **Dashboard — pending.** Useful onboarding, real counters, job link entry and actionable navigation.
5. **Browse jobs — pending.** Live search, useful default sources, role wording, filters, save/pass, posting details and source failures. Review and test unfinished local changes from the prior session.
6. **Application preparation/review — pending.** Verified profile to documents/answers; edit, compare, approve, persist and download. No invented experience.
7. **Execution and automation — pending.** Supported employer adapters, bounded execution, receipts, uncertain results, duplicates and limits. Never mark unsupported forms submitted.
8. **Inbox and tracker — pending.** Gmail consent, sync, routing evidence, reviewable drafts and real stage updates. Google credentials and user consent are dependencies.
9. **Networking and research — pending.** Useful persisted workflows and supported sources.
10. **Settings, assistant and API/MCP — pending.** Real provider status, private secrets, working assistant, token scopes/revocation and consistent permissions.
11. **Integrated release — pending.** Whole workflow, installed package, Linux checks, screenshots, backup/rollback, Git push, Azure deployment and public verification.

## Current limitations

The previous release is not feature-complete. Live AI is not wired, Gmail is unconfigured, and the worker has no enabled employer adapters. Live discovery requires manually configured sources. Stripe is in restricted test mode. These are open items.

## Entry-page evidence

Owner sign-in, logout and signed-in redirects exercised through the local browser. Desktop and 390px mobile owner/registration pages inspected. Password visibility toggles checked. Fifteen authentication tests, six registration tests and four customer authentication tests pass on Windows. Tests cover failed/throttled login, CSRF/origin checks, session expiry and revocation, customer creation, hashing, payment enforcement and owner isolation. Provider-dependent Google/OTP delivery remains blocked by missing configuration.

## Repair checkpoint: 16 October 2026

The entry release is on GitHub main and Azure. The following next-release work remains local pending integrated Linux and browser verification:

- Real on-demand search from six public company boards: 312 source postings fetched successfully, 77 related matches for frontend developer. Added null-safe source parsing, role wording and relevance ranking; 23 focused discovery tests pass.
- Billing plan/balance/top-up layout: 18 billing tests pass. Test Stripe configuration is preserved; real customer payments remain off.
- Resume draft import: recognizable work, education and projects, visible source review, fill-empty preservation, and explicit unverified status. Ambiguous history still needs correction.
- Profile editor layout, repeated-entry controls, document preparation entry from job details, readable application identities and shared navigation/typography.
- Review-first authorization, credit-reserve recovery and retry of untouched budget-blocked packages have regression proof. Explicit manual evidence resolves unsupported/uncertain applications without fabricated employer receipts or duplicate attempts.
- Gmail page traversal and bounded encrypted cursors; source and ingestion failures remain retryable. Live mailbox requires Google OAuth setup and user consent.
- Contacts can be edited, archived and restored. Tracker includes preparation and attention states.
- User chose Gemini instead of OpenAI. Gemini assistant and opt-in document/reply preparation are being integrated. OpenAI test rejected with credit_balance_exhausted; user chose not to add credits now. No further OpenAI requests are authorized for this repair.

Browser control became unavailable during Google sign-in (repeated connection timeouts). Do not claim final browser visual verification until it is restored. The user signed in to Google Cloud and is creating credentials privately; never reveal or commit them.

Open limitations: external automatic employer forms still require real, configured origin adapters. Only localhost submissions have been verified. On-demand lanes intentionally do not run unattended. No real employer submission, real customer payment or mail send is part of testing.


## Google configuration checkpoint

Private Google client ID/secret and both exact production callback URLs validated on 16 October. Gemini Interactions returned a completed response using the saved model. Settings transferred over SSH stdin into the root-only production environment; mailbox encryption key generated if absent. Gmail is configured, but connection and live mailbox routing require the account holder to grant read-only consent from Inbox. No mailbox was accessed and no email sent during setup. Full combined Linux regression is required before release activation.
