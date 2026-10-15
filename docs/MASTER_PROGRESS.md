# Master product progress — docs/superpowers/plans/2026-10-15-master-product.md

Approved input: user supplied identical local and GitHub observation documents and explicitly said "now proceed". Preserve native execution preference; use parallel domain work only where files and state are independent.

Baseline: GitHub main e44e53a, feature branch codex/jobpilot-web fast-forwarded to it; pending OCR patch retained. Previous final Windows suite 83 tests: 81 pass, two platform skips. OCR dependencies installed on Ubuntu; not yet deployed. Last deployed code 84edcf3 (resume intake), repository main includes documentation c8b28ae and observations e44e53a.

Ruling: Treat the user-supplied written handoff as the approved specification and proceed, rather than asking to approve the same requirements again. The earlier speculative TSENTA_PARITY_BLUEPRINT.md is superseded and not an authority.

Ruling: API-key creation decision was approved but local saving was declined. Do not create or write the key. Non-API domain work proceeds; live AI integration remains a clearly disclosed dependency.

Ruling: Keep Python/Flask/SQLite and original owner data. Request-scoped workspaces precede customer features; no global per-request config mutation. Cost if wrong is cross-account data exposure; explicit tests required.

Task 1: Windows repair and review complete; Linux/actual document verification pending.
Task 2: in progress.
Tasks 3–9: pending.

Update: master specification fast-forwarded from GitHub e44e53a. Three initial domain agents implemented profile forms/vault, discovery, and inbox; later runtime restart removed their agents, files retained. Replacement agents finishing billing (18 targeted tests passed), application worker/automation (9+4+3 targeted tests passed), and API/MCP (10 focused tests passed; shared-action integration follow-up running).

Root implemented Accounts verified identities/challenges (4 tests passed), customer workspace isolation (3 passed), customer auth routes/owner isolation (3 passed), initial application snapshots/approvals (agent strengthening), credit ledger/verified payment events (billing agent strengthened), normal workspace shell/mobile dock/pastel cards/inbox CSS, tracker/network/research/settings routes (3 tests passed). Registered profile, discovery, inbox, billing, APIs/MCP, application, automation modules. Protocol endpoints explicitly exempt session/origin/CSRF only where handlers validate bearer/signature/OAuth. OAuth authorization request survives owner/customer sign-in. Full integrated regression/browser/Linux review still pending.

User pasted an OpenAI secret in chat; warned to revoke/replace and save locally. Did not write, repeat, or use it. .env.local is absent as of last check; .env.* now ignored. No live AI implementation/request yet. Do not copy pasted secret into files or tools.

Firecrawl request: installed official firecrawl/skills catalog (34 skills, including 16 exact firecrawl-workflows mirrors) into C:/Users/sirri/.codex/skills using skill-installer helper. Installed official npm firecrawl-cli (59 packages). Status/smoke verification underway. API key absent in environment; keyless scrape may be available. Never use Firecrawl to bypass blocked Tsenta origin; only public permitted references/own public app. Firecrawl skills available next turn. Added .firecrawl/ ignore.

Caveman request: plugin search returned none, but local cached caveman/2.7.0 skills exist. Read caveman, lean-build, caveman-review; use valid build/review guidance, preserve developer-required clear prose/progress. Not a separate external coding agent. No external messages sent.

2026-10-16 checkpoint: resumed after usage interruption. Remote main is still e44e53a; prior rebuild was not pushed or deployed. Whole-branch review found two actionable defects. Account-wide pending subscription slots now prevent duplicate checkouts (10 billing web tests pass). Explicit fresh-package recovery now handles paused, never-attempted applications without retrying uncertain submissions (5 application web tests pass). Existing paused tasks remain auditable and new approvals are required.

Integrated Windows run before the final repairs: 194 tests, 18 errors sharing one LocalProxy/tempfile defect, one Unicode font failure, two platform skips. Both causes repaired: temporary directory receives str(home); bundled licensed DejaVu Sans preserves Polish PDF text. Targeted OCR: eight tests, seven pass and one Linux-only skip; Unicode regression passes. Fresh complete verification and Linux deployment remain pending. Do not claim full Tsenta parity or a completed deployment. Browser preview tool failed to initialize its kernel assets; no visual verification was obtained.

User requested a complete working application/source rather than a demo, while keeping token use tight. Current provider credentials are absent; never simulate AI, Gmail, SMS, payment or employer success. Existing deployment authorization remains in force. Preserve original owner data and avoid live employer submissions during checks.
