# Tsenta workflow reconstruction and JobPilot gap audit

Investigation date: 18 October 2026 (Europe/Warsaw). Target: https://tsenta.com/ and its authenticated web app. Implementation repository: megamind294/aijobFiller.

## Scope and current evidence

- REA MCP is connected: rea-agents 6.7.0, skill version 42. A session-status call succeeded; this establishes tool connectivity, not target analysis.
- A normal browser navigation to https://tsenta.com/ was explicitly rejected by the saved origin-permission policy. No REA, raw CDP, alternate browser, shell HTTP or scraping workaround was used to fetch the blocked target.
- Fresh runtime/source-bundle/network evidence is therefore unavailable. Browser or account access must be restored before collecting it.
- The user supplied historical observations from 15 October. Existing local screenshot/DOM evidence is under website-inspection/tsenta, outside this repository. Private captures are not published.
- No native desktop/mobile app binary or installer has been selected or supplied. Mobile web observations do not establish native app internals.

Read [reference workflows](REFERENCE_WORKFLOWS.md) for the historical page/control/action inventory and [JobPilot gaps](JOBPILOT_GAPS.md) for current implementation evidence. Neither is proof of private Tsenta infrastructure. Equivalent behavior should be independently implemented under JobPilot branding; reference source, logos, proprietary assets and private customer data are not product dependencies.

## Investigation checklist

| Question | Required proof | Current status |
|---|---|---|
| Entry, registration, onboarding | permitted browser session, actual form outcomes | historical observations only; fresh access blocked |
| Discovery/filter/ranking behavior | controlled query/filter scenarios, UI and response shapes | historical UI; suppliers and scoring internals unknown |
| Profile and document editing | edit/cancel/save, revision and rendered-document comparisons | historical inventory and limited edits |
| Preparation and application review | immutable attachments, answer edit/approval states | two historical prepared applications, both unsent |
| Submission and retries | explicitly authorized employer result/receipt, duplicate controls | not established; never infer success from a queue or debit |
| Inbox and tracker | user-approved connection, imported-message routing and corrections | historical UI; full live behavior unverified |
| Lanes and automated execution | bounded configured run and verified outcomes | controls observed; paid execution untested |
| Billing/credits/account lifecycle | authorized sandbox events, ledger and permissions | reference prices historical; private implementation unknown |
| Native/mobile/extension/MCP | selected package or permitted runtime and actual client scenarios | no package analyzed; interface claims are partial evidence |
| Visual/responsive parity | paired desktop/mobile screenshots and keyboard behavior | old captures available; current pages require permitted access |

## Independent implementation and release gates

Preserve Python/Flask/SQLite, account separation, owner data and explicit review before external actions. For each workflow: map entry/action/state/error/persistence; implement its service and UI; test permissions, invalid input, retries and persistence; inspect desktop/mobile; and verify the deployed end-to-end behavior. Keep real jobs, AI output, mail synchronization and payment state distinguishable from fixtures and provider configuration.

Do not label the product complete until each required workflow has proof or a named external dependency. Fresh inspection is currently blocked, so this checkpoint adds analysis documentation only and does not claim a completed clone or a deployed feature rebuild.
