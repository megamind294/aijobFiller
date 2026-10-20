# Tsenta desktop 3.3.0: bounded offline findings

Inspected 19 October 2026. Target supplied/selected by the user: `C:/Users/sirri/AppData/Local/tsenta_desktop/app-3.3.0/resources/app.asar`.

## Identity and evidence

- Package manifest identifies `tsenta-desktop`, version 3.3.0, entry `dist/main/index.js`.
- ASAR SHA-256: `29f8846bf60b3134e3e31f1c0858db60e147dc52232c9d321b0f1a0bec7fdbc3`.
- REA retained analysis: `ev_51f75d76293963cdccd870b33d5b1b89373e2856a6e86b2395778b4de2d877db`.
- Broad REA analysis reached resource limits: 402 JavaScript files parsed, 21,303 skipped. Its application and semantic coverage are partial. The client timed out, but the retained result was recovered through the Evidence summary. Dependency observations do not establish product behavior.
- Direct read-only ASAR header/member inspection located the manifest and startup source. No packaged code was executed and no embedded URL was fetched.

## Observed startup workflow

The readable startup entry creates an Electron splash screen, obtains a component manifest, chooses the first version compatible with the installed app version, and obtains each named component from a cache or download. The normal component path checks SHA-256 against the manifest before accepting cached/downloaded bytes. Components are placed in an in-memory bytecode cache; the loader then requires `index.jsc`. The source also contains network-error cache fallback paths and old-cache cleanup.

The entry constructs separate Windows/macOS release repository locations. These are static source observations, not evidence of an actual network request or a verified release service. The script sets up a remote-debugging port; this was not used to connect to a browser or bypass site access restrictions.

The packaged preload entry references compiled bytecode (`index.jsc`). The presence of automation libraries in dependencies is not proof of supported ATS workflows or successful submissions.

## Boundaries and unresolved questions

Focused REA follow-up recovered on 19 October: `ev_6deaff4ebf5f011bb03c2d8e0e9cc7a2d59c13ae6476abea0b105f2b0e65260f`. It selected the two package manifests and four startup/preload JavaScript files: 31,994 text bytes, four parsed JavaScript files, zero parse failures. The client delivery failed, but the completed retained record was recovered. Coverage remains partial because 31,248 inventoried files were deliberately outside source scope and semantic unknown retention reached capacity. This does not recover the absent compiled runtime components or prove product workflows.

- The startup source is a component loader, not the complete application implementation.
- No component `chunks` directory was found under the checked current user's `AppData/Roaming/Tsenta` path. This bounded check does not prove no cache exists elsewhere or that the app has never run.
- Job discovery, matching, document tailoring, recruiter email routing, application workers, billing rules, backend persistence, and supported ATS implementations remain unresolved by this artifact pass.
- Static source does not prove execution, reachability, server behavior, integrity enforcement in every fallback, or successful user workflows.
- Existing historical website captures remain separate evidence. No fresh access to previously denied website origins was attempted.

## JobPilot implications

Use this evidence to distinguish desktop packaging/update infrastructure from product functionality. Do not implement claimed employer coverage, server algorithms or mail routing based solely on package dependency names. Recreate observed user workflows using JobPilot-owned code and provider integrations; do not copy proprietary packaged source into the product.

Next useful evidence is the installed app's own cached component files, if present, or an authorized user walkthrough with screenshots. The installed bundle alone does not establish full Tsenta parity.
