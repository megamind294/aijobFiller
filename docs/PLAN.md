# JobPilot Implementation Plan

Goal: deliver a runnable local Python job-application assistant and setup guide.
Architecture: modular CLI, SQLite state, Playwright browser, read-only Gmail OAuth,
ReportLab resumes, openpyxl tracker. Spec: DESIGN.md. Python >=3.10.

1. Core and tracker: test canonical URL deduplication, required profile confirmation,
   recoverable state and Excel formula escaping. Implement core.py and tests.
2. Discovery and resumes: normalize public Greenhouse/Lever feeds and JSON/CSV imports;
   test selected facts cannot invent skills; implement sources.py and resume.py.
3. Browser and email: test field mapping, OTP freshness and exact sender/recipient;
   implement browser.py and gmail.py; require exact origin for custom mappings.
4. CLI and local demo: import, discover, prepare, apply, export, status, gmail-login,
   inbox and demo commands; user-reviewed submit and receipt proof; integration test.
5. Verify complete test suite, package install, PDF generation, Excel contents, demo.
   Package code, tests, profile/adapter examples, Windows setup and instructions.

Review focus: duplicate submission after crash; a remote label without Poland eligibility;
wrong recipient OTP; untrusted job text becoming spreadsheet formulas; mismatch between
resume facts and generated PDF. Tests belong to corresponding modules above.

## Completion
Implementation completed. All 21 tests passed, including two real local-browser
workflows. Reviewer findings (active claim, OTP origin recheck, profile validation,
metadata reimport) reproduced and fixed with regression tests. Live employer/Gmail
checks remain user-environment work, not claimed as tested.
