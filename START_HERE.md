# Start here — Windows

This is a local application assistant. It is not connected to your existing
ChatGPT Gmail session, and it does not run while your computer is off.

1. Install Python 3.10 or newer from https://www.python.org/downloads/.
2. Extract the ZIP to a normal folder (do not run inside the ZIP).
3. Double-click `setup_windows.bat`.
4. Open `data/profile.json`. Add your phone number, real work history, education,
   projects, languages and application answers. Review the starter skills. Set
   `facts_verified` to `true` only after checking everything. See README for examples.
5. Open PowerShell in this folder and use the commands below. Calling the venv
   Python directly avoids PowerShell's script activation restrictions.

```powershell
.\.venv\Scripts\python.exe -m jobpilot check
.\.venv\Scripts\python.exe -m jobpilot import examples/jobs.example.json
.\.venv\Scripts\python.exe -m jobpilot list
```

## Test without applying to a company

Run this in a second terminal and leave it open:

```powershell
.\.venv\Scripts\python.exe -m jobpilot demo
```

Back in your first terminal, copy the demo's ID from `list`:

```powershell
.\.venv\Scripts\python.exe -m jobpilot prepare YOUR_JOB_ID
.\.venv\Scripts\python.exe -m jobpilot apply YOUR_JOB_ID --adapter examples/adapter.demo.json
```

When the browser opens, type `fill` in the terminal. Review the form in the
browser, choose the sponsorship answer and check the demo consent box yourself.
Then type `submit` in the terminal and `SUBMIT` to confirm. The local demo
returns a receipt and Excel updates. No employer receives anything.

## Real jobs

Ask ChatGPT to research suitable roles and return `jobs.json` using the format
in `examples/jobs.example.json` (include the full description and actual
application URL). Download it here, then:

```powershell
.\.venv\Scripts\python.exe -m jobpilot import jobs.json
.\.venv\Scripts\python.exe -m jobpilot list
.\.venv\Scripts\python.exe -m jobpilot prepare YOUR_JOB_ID
.\.venv\Scripts\python.exe -m jobpilot apply YOUR_JOB_ID
```

Open the generated PDF in `data/resumes/YOUR_JOB_ID/resume.pdf` before applying.
The script opens the job website. You navigate to the form and handle login.
Type `fill` to populate recognized fields. Complete unknown questions and custom
widgets in the browser. For sites without a verified adapter, click the real
submit button yourself, then type `submitted` and record the actual receipt.

Never reuse the demo adapter for an employer. A site's adapter needs its own
exact origin, controls and success message. See README.

## Gmail and Excel

Excel: `data/applications.xlsx` (close it before the script exports updates).
SQLite: `data/applications.sqlite3` (the authoritative history).

Gmail is optional. Follow the Desktop OAuth instructions in README, place
`credentials.json` inside `data`, then run:

```powershell
.\.venv\Scripts\python.exe -m jobpilot gmail-login
.\.venv\Scripts\python.exe -m jobpilot inbox
```

Do not upload your `data` folder, browser session or OAuth credentials to GitHub.

## New: continuous discovery and preparation

Double-click `start_background.bat` after setup. Every 20 minutes it reads your
configured company feeds, imports files in `data/incoming`, prepares up to five
new resumes and refreshes Excel. It restarts after an unexpected process exit.
Review and submit applications separately using `apply` or `run`.

To start it whenever you sign into Windows, right-click
`install_startup_windows.ps1` and run it with PowerShell, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_startup_windows.ps1
```

The process execution-policy argument applies only to this invocation of the
local installer script. Review the script before running it. No system-wide
execution policy is changed. Windows organization policy may block registration.

Check or stop the worker:

```powershell
.\.venv\Scripts\python.exe -m jobpilot health
.\.venv\Scripts\python.exe -m jobpilot stop
```

Your computer must be on, awake and online. This does not make your laptop run
while switched off. The installer has not been run on your computer by ChatGPT.
CAPTCHAs need you: solve them in the visible browser, then type `SOLVED` in the
terminal. There is no CAPTCHA bypass or paid solver integration.
