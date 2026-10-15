# Execution ledger — plan: docs/superpowers/plans/2026-10-15-jobpilot-web.md

Approved scope and execution method: browser interface and Azure rollout, implemented inline.

Pre-flight: Task 1 produces create_app/auth used by Tasks 2–4; all share JOBPILOT_HOME and Flask blueprint registration. Task 5 consumes packaged WSGI application and production config from Task 4.

Ruling: Use the dedicated fresh repository clone on a new feature branch as the isolated workspace, avoiding an additional worktree and another permission checkpoint. This clone contains no preexisting user code changes; deployment uses a separate VM release. Cost if wrong: an additional checkout may be needed later.

Ruling: Persist authentication in a private home/web-auth.json file, with initial PASSWORD_HASH/AUTH_VERSION accepted only for tests. Keep session secret in the root-owned systemd environment file. Password changes need writable credential storage and dynamic version checks. Cost if wrong: adjust the authentication interface before deployment.

Task 1: in progress. Auth tests written before implementation.

Task 1: Authentication tests RED (missing web module) to GREEN (6/6). HTTPS test requests now send the real browser Referer header required by Flask-WTF.

Ruling: Close Store SQLite connections explicitly after their transaction context. Existing core tests failed on Windows because the original connection context committed but left handles open, preventing temporary data cleanup. All callers use with store.connect(); transaction semantics remain unchanged. Cost if wrong: database access regression, covered by the existing suite.

Task 1: complete (commit edf1e4c; auth tests 6/6, existing core tests 9/9).
Task 2: complete (job-route tests RED 404/missing actions -> GREEN 8/8; commit pending combined functional checkpoint).
Task 3: complete (settings tests RED missing routes -> GREEN 5/5).
Task 4: in progress (responsive interface, full-suite and browser QA, production configuration).

Task 4: Full suite 47/47 passing, including original local browser workflow. Real-browser QA passed login, dashboard, manual job creation, resume PDF download, profile save, status confirmation, Excel download, and four narrow-screen routes. Screenshots visually inspected; added visible mobile account/sign-out controls.

Final review: fresh reviewer found three Important issues and no minors. Fix pass: reject resume path/component symlinks (metadata regression RED 200 -> GREEN 404; real Linux-link test scheduled before service start); preserve unsaved profile fields/raw nested JSON after validation failures (RED lost edits -> GREEN preserved); bound discovery to a 30-second budget, 5-second socket timeout, 2 MiB responses, five Lever pages and 500 results (three RED missing-budget tests -> GREEN). Added expired-session coverage. Final full-suite rerun in progress.

Final: Ruling: reviewer deferred live DNS/TLS/firewall/service health to rollout checks — these will be verified on the VM before delivery; cost if wrong is an inaccessible site.
Final: Ruling: real company-feed availability remains external and is checked on demand; mocked feeds test deterministic behavior without changing the real queue. Cost if wrong is a reported source error, not a false application submission.
Final: Ruling: Gmail, paid AI, and employer submission remain outside this approved release, as documented; cost if wrong is a later feature request.

Task 5: Private-data backup created at /var/backups/jobpilot-web/preweb-20261015T085318Z.tar.gz. Original VM checkout revision 9b2c3f4e6281bcf7ee54b30104176f685a2f8cb2 is clean and preserved. Installed distro Caddy and prerequisites. Azure hostname assigned: jobpilot-1341125888.polandcentral.cloudapp.azure.com. No web app service started yet.

Final: all three Important review findings fixed with RED-to-GREEN regressions. Final Windows suite: 54 tests, 53 passed, one skipped because native file symlinks require Windows privilege. That exact real-link case must pass on Linux before starting the service. Updated-app real-browser QA passed again; Python compilation passed. No deferred minors.
