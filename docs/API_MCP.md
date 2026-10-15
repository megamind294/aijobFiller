# Versioned API and ChatGPT MCP connection

These endpoints reuse the web application's account workspaces and application-package service. They do not call an AI provider, expose provider credentials, or run operating-system commands. Confirmed approvals use the website's shared service to reserve credits and queue browser work; HTTP handlers do not contact employers directly.

Set `MCP_PUBLIC_URL` in Flask configuration to the canonical HTTPS origin (for example `https://jobs.example.com`, without `/mcp`). OAuth metadata is disabled until this is configured. The deployment must terminate TLS and forward the correct host/protocol through the existing trusted-proxy configuration. The remote MCP URL is that origin followed by `/mcp`.

## API

All `/api/v1` routes require an opaque Bearer token in the Authorization header. Browser session cookies do not authenticate API calls. Tokens bind the server-resolved account, its auth version, granted scopes, expiry, and the `/mcp` resource audience. Changing an account's auth version revokes access. Tokens and OAuth codes are stored as SHA-256 digests in `api-grants.sqlite3` in the private application home.

| Route | Scope | Result |
| --- | --- | --- |
| GET `/api/v1/profile` | `profile:read` | Source profile and SHA-256 `version` |
| GET `/api/v1/jobs` | `jobs:read` | Up to 500 saved jobs, each with fact `version` |
| GET `/api/v1/applications` | `applications:read` | Package states and profile/job/document/form hashes |
| POST `/api/v1/applications/prepare` | `applications:write` | Prepare factual documents; no employer submission |
| POST `/api/v1/applications/approve_documents` | `applications:write` | Approve exact reviewed documents and queue form filling |
| POST `/api/v1/applications/approve_form` | `applications:write` | Authorize submission of the exact reviewed form and queue submission |

Write requests require JSON `confirmed: true`, `profile_version`, and `job_version`. Preparation also requires `job_id` and an idempotency `request_key`; optional `cover_letter` is boolean and `optimization` is `off` or `honest`. Document approval requires `application_id` and `document_hash`. Form approval additionally requires `form_hash` and constitutes explicit authorization to submit that exact reviewed form. Stale or missing confirmation returns 409; authentication errors return 401. Approvals call the website's shared credit/cap reservation and queue service, so retries retain one reservation and one task per action. Review actual documents and employer form in the web interface before confirming approval.

Signed-in users can create a scoped 30-day personal API token at `/connections/api`. It is shown once in a no-store response. Revoke whole account grants at `/connections/chatgpt`; this invalidates related access and refresh tokens.

## MCP / OAuth

`POST /mcp` implements stateless JSON-response Streamable HTTP for stable MCP versions 2025-11-25, 2025-06-18 and 2025-03-26. Supported methods are `initialize`, `ping`, `tools/list`, `tools/call`, and initialized/cancelled notifications. JSON-RPC batches, server-initiated events, resource subscriptions and resumable SSE are not supported; GET/DELETE return 405. Clients must accept both application/json and text/event-stream. The server checks Origin and MCP-Protocol-Version headers when present. No session ID is needed.

Read tools: `get_profile`, `list_jobs`, `list_applications`. Action tools: `prepare_application`, `approve_application_documents`, `approve_application_form`. Discovery filters tools by granted scopes. Calls validate argument names, scopes, explicit confirmation, and source hashes. Tool output omits local file paths, stored provider settings and raw exception messages.

OAuth discovery routes:

- `/.well-known/oauth-protected-resource/mcp` (also root alias)
- `/.well-known/oauth-authorization-server`

Protocol endpoints: `/oauth/register`, `/oauth/authorize`, `/oauth/token`, `/oauth/revoke`. Authorization requires a signed-in account, explicit browser consent with the web CSRF token plus one-time session nonce, exact registered redirect URI, matching `/mcp` resource indicator, and PKCE S256. Authorization codes expire in five minutes and are single-use. Access tokens last one hour; refresh tokens last 30 days and rotate on each use. Refresh-token reuse revokes the grant. Only public OAuth clients with HTTPS redirects and authorization_code/refresh_token grants are accepted. Dynamic registration stores only name, redirect URIs, and supported grant/auth methods, with a bounded hourly registration rate. Client ID metadata documents and confidential-client secrets are not implemented.

Connect this HTTPS `/mcp` server through the host's custom MCP connection UI and choose OAuth. This code has not registered or published a plugin, provisioned a public deployment, or completed an external ChatGPT handshake. It requires a reachable TLS deployment and configured customer sign-in to complete that integration.

## Integration and checks

Call `register_product_api(app)` before `register_mcp(app)`. Session middleware exempts the bearer API and protocol/discovery endpoint names only. The OAuth authorize handler must be reachable before login so it can preserve the request and require its own authenticated consent. Browser authorize, personal-token creation, and connection revocation keep CSRF protection. Never exempt a whole URL prefix or all blueprint endpoints.

Focused verification: `.venv/Scripts/python.exe -m unittest discover -s tests -p test_product_api.py`.

Protocol references: [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization), [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [OpenAI authentication](https://developers.openai.com/plugins/build/auth).
