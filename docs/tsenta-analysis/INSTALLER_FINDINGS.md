# Tsenta installer: bounded offline findings

Date: 2026-10-19. Target selected by the user: `C:/Users/sirri/Downloads/Tsenta Installer.exe`.

## Scope and provenance

The file was read as data only. It was not installed or launched. No embedded URL was fetched; no Tsenta website, browser session, installed application or account was accessed. These findings are ordinary Python/Windows static observations, **not REA Evidence IDs**. The installed REA public CLI was attempted with `--help`; it produced no output within several minutes and was stopped. No REA analysis engine or configuration was installed/changed.

## Observed

- Size: **813,088 bytes**; SHA-256: `3e3d1d64269301acc0f630253c31fe3872e6c2d6dcd62aff525805e7574508b3`.
- PE signature is valid at file offset 128; machine `0x14c` (x86). Sections: `.text`, `.rsrc`, `.reloc`.
- A CLR directory is present (RVA 8200, size 72). This is a managed Windows executable, not an observed Electron ASAR/application bundle.
- Windows version resource identifies **Store Installer**, **Microsoft Corporation**, original filename **StoreInstaller.exe**, file version **22609.1501.4.0**. A resource label is a claim embedded in the file, not proof of trust.
- Embedded signer certificate subject: `CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US`; issuer `Microsoft Marketplace CA G 023`; thumbprint `0F7E3531DB084A3D45759670B7699E53985E8742`. Extracted without verification via Windows certificate parser. Certificate validity dates alone do not establish Authenticode validity because timestamp and chain validation were not performed.
- PE certificate directory occupies file offset 783872, size 29216. Presence alone does not validate signature integrity, trust or revocation.
- Embedded strings include Microsoft Store acquisition/package retrieval endpoints and methods, including `PackageRetrievalInstaller.RunInstallSequenceAsync`, `DownloadFrameworkIntoSlotAsync`, `OpenStore` and `LaunchProductAsync`. Strings establish static presence, not successful runtime behavior.
- Store-shaped identifiers include `9NBLGGH4NNS1`, `9WZDNCRFJBMP`, and `9PLM9XGG6VKS`. **Their roles and which belongs to Tsenta have not been resolved**; do not treat every identifier as the requested application.

## Inferred

The combined metadata, small size, CLR layout, Store UI resources and acquisition strings strongly indicate a **Microsoft Store installation bootstrapper**, rather than the actual Tsenta desktop application. Renaming an installer to “Tsenta Installer.exe” does not establish that its embedded code implements Tsenta's product workflows.

Candidate generic installation sequence (inferred from string names only): resolve Store product/package → check compatibility/entitlement → retrieve package/framework dependencies → install → launch product or fall back to Store. This is **not** an observed Tsenta job-search/application workflow.

## Unverified and missing

This artifact does not presently establish Tsenta resume parsing, matching, application automation, inbox routing, billing, API contracts, local database, or server implementation. No decompilation or runtime trace completed. Public browser-origin access remains outside this offline scope.

For substantive desktop workflow analysis, the missing input is the **actual installed Tsenta application package or an offline copy of its installation directory** (for example MSIX/AppX, or an Electron executable plus `resources/app.asar`, if that is its real packaging). The actual package identity and architecture must be established from that selected artifact, not guessed from this bootstrapper. The installer will not be executed or used to fetch a package implicitly.

## Evidence files

Local evidence under `website-inspection/tsenta/installer-analysis/`: `static-identity-strings.json`, `pe-layout-identity.json`, `version-info.json`, `certificate-inventory.json`, `embedded-signer.json`. They retain offsets and original artifact digest. No proprietary application source was copied into JobPilot.
