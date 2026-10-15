# Execution ledger — plan: docs/superpowers/plans/2026-10-15-jobpilot-web.md

Approved scope and execution method: browser interface and Azure rollout, implemented inline.

Pre-flight: Task 1 produces create_app/auth used by Tasks 2–4; all share JOBPILOT_HOME and Flask blueprint registration. Task 5 consumes packaged WSGI application and production config from Task 4.

Ruling: Use the dedicated fresh repository clone on a new feature branch as the isolated workspace, avoiding an additional worktree and another permission checkpoint. This clone contains no preexisting user code changes; deployment uses a separate VM release. Cost if wrong: an additional checkout may be needed later.

Ruling: Persist authentication in a private home/web-auth.json file, with initial PASSWORD_HASH/AUTH_VERSION accepted only for tests. Keep session secret in the root-owned systemd environment file. Password changes need writable credential storage and dynamic version checks. Cost if wrong: adjust the authentication interface before deployment.

Task 1: in progress. Auth tests written before implementation.

Task 1: Authentication tests RED (missing web module) to GREEN (6/6). HTTPS test requests now send the real browser Referer header required by Flask-WTF.

Ruling: Close Store SQLite connections explicitly after their transaction context. Existing core tests failed on Windows because the original connection context committed but left handles open, preventing temporary data cleanup. All callers use with store.connect(); transaction semantics remain unchanged. Cost if wrong: database access regression, covered by the existing suite.
