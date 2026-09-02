# Desktop framework scorecard

No framework is selected merely because its development stack looks appealing.
Both candidates must package and launch the same authenticated Python sidecar on
a native Apple Silicon runner first.

## Automated evidence

The `Desktop Framework Spikes` workflow records:

- runner and executable architecture,
- code-signature verification,
- packaged sidecar startup time,
- authenticated local health-job completion time,
- full app-to-sidecar startup time,
- application bundle size,
- process/restart cleanup behavior,
- and downloadable `.app` bundles for both candidates.

## Manual evidence still required before selection

A hosted runner cannot fairly judge:

- candlestick zoom/pan smoothness,
- keyboard and accessibility behavior,
- native menu/window feel,
- perceived cold-start quality on the user's Mac,
- and the effort required to port the real Profit First workflow.

The first framework decision therefore requires both automated artifacts plus one
small real chart-and-Progress UI comparison on an Apple Silicon Mac. It does not
require migrating the full application twice.

## Decision rule

Prefer the candidate that satisfies all hard gates and wins most high-value
criteria. A small bundle-size advantage cannot outweigh unreliable sidecar
startup, poor charts, broken recovery, or duplicated trading logic.

| Criterion | Hard gate | Weight |
|---|---:|---:|
| Sidecar starts and authenticates | yes | 5 |
| Existing Python engines remain shared | yes | 5 |
| Job recovery and cancellation remain intact | yes | 5 |
| Chart interaction quality | yes | 5 |
| Packaged Apple Silicon launch | yes | 5 |
| Startup time | no | 4 |
| UI development speed | no | 4 |
| Accessibility/native controls | no | 3 |
| Bundle size | no | 2 |
| Signing/notarization path | yes | 4 |
