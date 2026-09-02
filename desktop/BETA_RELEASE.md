# Trading Intelligence desktop beta release gates

The current desktop package is an **internal Apple Silicon beta**, not a public macOS release.

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

## Public release gate

A build must **not** be published as a public release or used by an automatic updater until all of these are true on the final packaged app:

- the code signature is valid,
- the signature chain contains `Developer ID Application`,
- Apple notarization has succeeded,
- the notarization ticket is stapled to the app/package,
- Gatekeeper accepts the final app,
- the same signed/notarized artifact passes the full desktop smoke test,
- rollback/recovery has been tested.

`scripts/check_desktop_release_readiness.py --require-public-ready` is the fail-closed gate for those conditions.

## Why automatic updates are not enabled yet

An updater creates a software-supply-chain path into the trading application. It should only be added after the signed/notarized release channel exists and update packages can be cryptographically verified before installation.

Until then, beta builds are manually installed from the verified DMG/ZIP artifact and do not replace or disable the Streamlit web app.
