# JobPilot page repair checklist

User resumed work on 16 October 2026: review every page, including appearance and real behavior. The master observations remain the target. Preserve the Python/Flask/SQLite architecture, account isolation and owner data.

A page passes only when its primary workflow, invalid input, empty/error states, persistence, permissions and desktop/mobile rendering have been checked. A successful GET request is not workflow proof. External-service blockers remain open. Verification does not include live employer submissions or real payments.

## Delivery order

1. **Sign-in and registration — local verification passed; Linux/public verification pending.** Shared responsive design, clear customer/owner entry, password visibility, validation, session redirects, account creation and payment gate. Google/OTP delivery requires provider credentials.
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
