#!/usr/bin/env bash
# Start the robot dashboard on the Pi: http://<pi-ip>:8080
# Uses the same Python environment as main.py (the .venv next to this file if present).
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" web_app.py "$@"
