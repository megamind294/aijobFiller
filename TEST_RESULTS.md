# Verification report — 15 October 2026

## Passed

`python -m unittest discover -s tests -v`: **28 tests passed**, zero failures.

- Canonical URL handling and preservation of distinct job IDs.
- Duplicate imports preserve submitted status.
- Uncertain submissions cannot be automatically retried.
- Exclusive claim while browser review remains active.
- Reimport can repair job descriptions without resetting application history.
- Verified profile requirements and invalid nested data rejection.
- Excel formula-injection escaping; real workbook creation/history contents.
- Resume skills/achievement preservation, ranking and original dates.
- Real PDF creation; the demo PDF was rendered and visually inspected.
- OTP freshness, exact sender and recipient checks, ambiguous-code rejection.
- Recheck website origin after waiting for Gmail.
- Sensitive answers are not guessed.
- Untrusted iframe origins are excluded from common form filling.
- Work history can be addressed by list-index paths in adapters.
- Location filter excludes remote-US-only jobs from Poland matches.
- A real local Chromium application form was filled, uploaded with a generated
  PDF, submitted once, and its visible receipt detected. SQLite was marked
  SUBMITTED; duplicate application was blocked.
- Real browser session remained exclusively claimed after autofill and changed
  to NEEDS_REVIEW only when paused.

Other checks: CLI init/import/list, help output, Python compileall and wheel build.

## Environment and limits

Tests used Python 3.12.14 on Linux and Playwright 1.51.0 with Chromium 134, used
only against the local test server. The newest browser download failed in this
environment; an older available version allowed the local integration tests.
The install requirements permit newer Playwright releases. Use an up-to-date
browser locally; no guarantee is made about untested version/site combinations.

Not tested here: Windows installer execution, live employer forms, real Gmail
OAuth login, live OTP delivery, paid OpenAI calls and live company-feed responses.
No real employer was contacted and no live application was submitted.

The Gmail and AI tests validate local logic, not account authorization. OTP support
requires a configured exact site rule. Site-specific selectors must be verified
before using the submit command. Multi-step navigation and custom widgets remain
human-assisted. The independent reviewer supplied four important findings; all
four received regression tests and were fixed. Its final review report was not
completed, so no claim of a completed independent audit is made.

## Version 1.1 additions

Seven additional tests pass: worker process locking/release; incoming job import
and no submission; malformed-file isolation; bounded retry delay; real-browser
CAPTCHA detection with observation of an existing provider token; resume locking
during browser sessions; graceful worker stop. CLI watch --once and health passed.
The 1.1 wheel builds and Python compilation succeeds. Windows startup registration
and long-duration uptime are not tested here. CAPTCHA handling is a manual handoff,
not an automated solver or guarantee of detecting every challenge.
