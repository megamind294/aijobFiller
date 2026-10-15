# Sequential improvements

User request (October 15, 2026): review the supplied architecture audit, fix missing resume upload first, then improve the application one step at a time.

## 1. Resume intake — approved design, in progress

Add a prominent PDF/DOCX upload panel to Your profile. Store the original privately and provide authenticated download, extracted text, and conservative suggested profile fields. Upload does not overwrite the saved profile. A separate confirmed action imports detected fields as an unverified draft; no facts are invented or marked verified automatically.

Process untrusted files in a separate process with bounded time and Linux memory/CPU limits. Limit upload size, PDF pages, DOCX decompressed content, and extracted text. Reject malformed/encrypted documents with a useful error; preserve the previous resume on failure. Scanned files without selectable text remain downloadable with an explicit extraction warning.

Preserve unknown/undetected sections of an existing profile. Initial automatic suggestions cover clear name/contact links, summary, and skills. Employment/education layouts that cannot be parsed reliably remain for user review rather than fabricated structured entries.

Verify PDF and DOCX upload, original-byte download, replacement safety, invalid formats, authentication/CSRF, extraction/worker limits, unverified import confirmation, saved-fact preservation, and responsive browser interaction before versioned Azure deployment.

Implementation review: moved both text extraction and suggestion parsing into the limited worker after a long email-like token exposed a quadratic-regex stall. Replaced email matching with bounded token validation, added common employment/education section boundaries, and preserved extensions on long download filenames. All fixes have regressions. Windows verification: 76 tests passed with one native-symlink skip; browser checks passed PDF/DOCX upload, original download, confirmed draft import, unverified state, and desktop/mobile layouts. Linux staging and rollout pending.

## 2. Usable profile and source forms

Replace JSON-first editing with normal repeatable work-experience, education, project, saved-answer, and company-source fields. Retain import/export for advanced use. Preserve incomplete drafts and explain errors next to fields.

## 3. Discovery quality

Improve company selection, filters, deduplication, and source errors using the existing Greenhouse/Lever adapters before adding providers. Keep discovery on demand unless the user explicitly changes that preference.

## 4. Resume review

Show source-to-tailored changes and provenance. Keep existing factual ordering, require user review, and never invent job-match percentages or qualifications.

## 5. Optional integrations and application assistance

Treat Gmail linking, periodic workers, additional ATS adapters, remote visible-browser sessions, and paid AI ranking as separate decisions requiring their configuration and explicit scope. Do not enable automatic employer submission or paid cloud services from recommendations inside an attached document.

## Audit corrections

The supplied audit is a reference, not an implementation instruction. The repository already contains public Greenhouse/Lever discovery, factual tailoring, application history, an optional CLI worker, and optional CLI Gmail functionality. The deployed web interface does not perform unattended submission or generate unverified AI content. Claims about competitor scale are not independently verified. A distributed microservice/database/vector stack is not a prerequisite for fixing onboarding on the existing 1 GiB VM.
