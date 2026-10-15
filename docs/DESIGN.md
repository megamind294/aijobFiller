# JobPilot design
A local, single-user Python assistant for Rinkle's React/frontend job search in Poland.

The user asked for job discovery, complete application details, tailored resumes,
Gmail verification and Excel tracking. Build working local software now; no live
applications are sent during development. Final submission remains user-reviewed.

Workflow: public ATS discovery or ChatGPT JSON/CSV import -> persistent SQLite queue
-> verified profile -> per-job factual resume PDF -> visible browser form filling
-> optional narrowly scoped Gmail OTP -> explicit submit -> success evidence -> Excel.

Modules: core (validation/state), sources (public feeds), resume (fact selection and
PDF), browser (common controls and configured selectors), gmail (OAuth read-only),
cli (workflow), demo (local application server). SQLite is authoritative; Excel is
an atomic export with an event history. Never mark a click as a completed application.

A configurable exact origin binds custom selectors and OTP rules to a site. Unknown
questions, consent, demographics, work authorization and passwords are never guessed.
User can complete them manually and rerun filling. Support native HTML controls and
iframes; custom widgets and site-specific multi-step navigation remain manual.

Resume tailoring reorders existing skills and achievements. Optional AI ranks IDs,
never authors new experience. The master profile and dates remain intact. Each job
retains the rendered resume and its facts. Do not infer visa rights from residence.

Discovery supports Greenhouse and Lever public boards; any board/company URL can
be imported. No LinkedIn scraping, CAPTCHA bypass or universal ATS claim. Gmail
requires the user's separate Desktop OAuth credentials. Codes are held in memory;
no mailbox bodies or tokens are written to Excel. Verification links are shown for
manual opening only after exact hostname allowlisting.

Tests: duplicate imports, blocked retry after uncertain submission, formula injection,
profile validation, fact preservation, OTP freshness/sender/recipient, discovery
normalization, browser form filling/actual receipt in a localhost demo.

Version 1.1: continuous discovery/import/prepare worker; OS process locks; rotating
logs; heartbeat and stop file; Windows login launcher; CAPTCHA detection and human
handoff. No unattended applications, CAPTCHA solver or bypass.
