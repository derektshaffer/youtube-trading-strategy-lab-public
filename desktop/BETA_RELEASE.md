# Trading Intelligence desktop beta release gates

The default desktop package is an **internal Apple Silicon beta**, not a public macOS release.

## Internal beta gate

`.github/workflows/desktop-beta-package.yml` must:

1. run the desktop regression suite,
2. build the existing Python sidecar,
3. build the production PySide6 app,
4. launch the complete app smoke test,
5. create both a DMG and ZIP,
6. verify the DMG after packaging,
7. verify arm64 architecture and code signatures,
8. publish SHA-256 checksums and a machine-readable manifest,
9. classify the artifact as `internal_beta_only`.

No secrets are embedded in the app or release package. Runtime provider credentials remain in the macOS Keychain / private cloud environment.

## Notarized candidate gate

`.github/workflows/desktop-notarized-candidate.yml` is manual-only and runs only from `main`. It requires protected GitHub Actions secrets for:

- `APPLE_DEVELOPER_ID_P12_BASE64`
- `APPLE_DEVELOPER_ID_P12_PASSWORD`
- `APPLE_NOTARY_APPLE_ID`
- `APPLE_NOTARY_PASSWORD` (an app-specific password)
- `APPLE_TEAM_ID`

The workflow imports the Developer ID certificate into a temporary keychain, stamps final bundle metadata **before** signing, applies a Developer ID Application signature with hardened runtime and a secure timestamp, runs the desktop smoke test, notarizes and staples the app, packages the already-signed app without modifying it, then signs/notarizes/staples the DMG as well. Finally it mounts the DMG and re-runs the public-readiness check on the contained app.

The temporary signing keychain is deleted at the end of the workflow. No certificate or notary credential is included in the generated app, DMG, ZIP, manifest, logs, or repository files.

This workflow uploads a `notarized_candidate` artifact only. It does **not** create a GitHub Release.

## Public release gate

A build must **not** be published as a public release or used by an automatic updater until all of these are true on the final packaged app and DMG:

- the code signature is valid,
- the signature chain contains `Developer ID Application`,
- hardened runtime is enabled,
- a secure timestamp is present,
- Apple notarization has succeeded,
- the notarization ticket is stapled,
- Gatekeeper accepts the final app and DMG,
- the mounted DMG contains the same accepted arm64 app,
- the same signed/notarized artifact passes the full desktop smoke test,
- rollback/recovery has been tested.

`scripts/check_desktop_release_readiness.py --require-public-ready` is the fail-closed app gate. `scripts/finalize_desktop_distribution.py` additionally requires the final DMG to pass signature, stapling, and Gatekeeper checks before it creates the distribution manifest and checksums.

## Why automatic updates are not enabled yet

An updater creates a software-supply-chain path into the trading application. A notarized app is necessary but not sufficient for safe updates. The update channel must also verify update packages cryptographically and have a tested rollback path.

For that reason, the notarized candidate manifest deliberately records `automatic_updates_ready: false`. Automatic updates remain disabled until that separate update-signature and rollback system exists.

Until then, beta builds are manually installed from a verified DMG/ZIP artifact and do not replace or disable the Streamlit web app.
