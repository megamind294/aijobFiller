# Safe Browsing investigation — 16 October 2026

Google Search Console ownership was verified with explicit user approval using its HTML verification file. The Security Issues report identifies one category, **Deceptive pages**, with samples `/` and `/login`. Google has not specified the exact triggering page element. Do not claim a confirmed false positive or that the warning is cleared before Google confirms review.

Evidence: 86 deployed Python/template/script/style files matched the committed source hashes. Expected application/Caddy services and listening ports were present. HTTPS validation succeeded. These checks are limited and do not constitute a comprehensive compromise assessment.

Remediation: anonymous visitors now see a public JobPilot service description rather than an immediate owner-login redirect. Public privacy information explains storage, optional read-only Gmail, optional Gemini processing, Stripe test mode and operational limits. The owner login explicitly requests only this installation's JobPilot credentials, not another provider's password. The page identifies the independent source project and disclaims affiliation with Google, Microsoft, LinkedIn and employers. Private routes retain authentication and paid-access enforcement. The Google verification file is retained through the Caddy configuration.

Validation: 16 authentication tests passed on Windows. Full Linux regression and review submission outcome must be recorded after completion. The external warning may persist until Google's review and browser-list updates; do not bypass browser protection or rotate domains to evade the listing.


Outcome: all 285 Linux tests passed in 136.760 seconds. Tested tree 60d669fc0958f5ba2b947f910356157bca579c41 deployed, backup /var/backups/jobpilot-web/master-20261016T201048Z. Public service/privacy and retained verification file checked over HTTPS; all configured provider settings preserved. Google Search Console confirmed **Request submitted successfully.** The review explicitly states the original trigger is unconfirmed and no specific malware payload removal is claimed. Warning removal is not verified and remains dependent on Google review. Local screenshot evidence is saved outside Git under .web-preview/google-review-submitted.jpg.
