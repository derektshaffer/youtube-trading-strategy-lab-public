#!/bin/bash
# Finder starts commands in an arbitrary directory with a minimal PATH.
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
unset PYTHONHOME PYTHONPATH
trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Trading Lab Dev could not start. See the error above."; if [ -t 0 ]; then read -r -p "Press Return to close."; fi; fi' EXIT
for candidate in \
  .venv-dev/bin/python .venv/bin/python venv/bin/python \
  python3.12 python3.13 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
  /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    "$candidate" scripts/launch_desktop_dev.py "$@"
    exit 0
  fi
done
echo "Python 3.11 or newer is required. Install Python from python.org, then double-click again."
exit 1
