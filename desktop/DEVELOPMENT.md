# Trading Lab Dev on macOS

Double-click **Trading Lab Dev.command** in the repository root. The first
launch installs the existing desktop requirements into `.venv-dev` if no
compatible `.venv` or `venv` exists. Python 3.11+ and internet access are needed
for that first setup. Subsequent launches use installed dependencies; ordinary
code edits need no download, bundle build, signing, or installation.

1. Update/edit **this checkout**.
2. Quit **Trading Lab Dev** (Command-Q or close its window).
3. Double-click **Trading Lab Dev.command**.
4. Test your change.

The window title says **Trading Lab Dev — Local Source** and the status bar
shows the exact checkout. Finder's starting directory does not matter. Keep
the command file beside `desktop/` and `scripts/`; create a Finder alias if you
want a shortcut elsewhere. A second launch while Dev is running reports that
you should quit it first.

The current production shell is PySide6, with a separate authenticated Python
engine. Both run from this repository under the same Python interpreter.
There is no hot reload: restarting reloads both UI and engine source. The
Tauri spike and release packaging scripts are not part of this workflow.

Dev owns `.desktop-dev/data/` (settings, databases, jobs, logs and caches).
First launch copies existing non-secret desktop and scanner-launcher settings
from `~/Library/Application Support/Trading Intelligence Lab`. Later dev edits
stay in the dev folder; stable settings are never overwritten. Existing macOS
Keychain credentials are used as a read-only fallback, and credentials saved
from Dev go to a separate `Trading Intelligence Lab Dev` Keychain service.
macOS may ask permission for Python to read existing credentials. Development
uses the configured real library/providers when you choose those workflows;
its cloud jobs still run against the configured GitHub repository. Existing
stable local job history and in-progress jobs are not copied or resumed.

The installed Trading Intelligence app, release artifacts, and other checkouts
are unchanged. Changes made in a different checkout do not appear in Dev.
No branch is merged or updated automatically by launching it.

Troubleshooting: keep the launcher's Terminal window open for startup errors;
engine logs are in `.desktop-dev/data/desktop-service.log`. Launch provenance
can be checked in `.desktop-dev/data/dev-launch.json`, which records the
source paths and window title without credentials. Changes to requirements
trigger installation on the next launch. If a dev virtual environment becomes
unusable or the checkout is moved, remove only `.venv-dev` and relaunch to
recreate it. Do not remove `.desktop-dev` if you need its saved job history.

For isolated fixture verification:

```bash
./"Trading Lab Dev.command" --smoke --data-dir /tmp/trading-lab-dev-smoke \
  --library-fixture /absolute/path/library.json --metrics-output /tmp/dev-smoke.json
```
