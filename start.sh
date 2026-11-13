#!/usr/bin/env bash
# FieldVision: start the FastAPI backend locally.
#
#   ./start.sh              serve on http://localhost:8000
#   ./start.sh --reload     restart on code changes (development)
#   PORT=8001 ./start.sh    use another port
#
# Needs a .env file (cp .env.example .env) and the dependencies in
# requirements.txt, in .venv or on your PATH.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env file found. Create one from the example and fill it in:"
  echo "    cp .env.example .env"
  exit 1
fi

# Prefer the project's virtualenv; fall back to whatever python is on PATH.
if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "No python found. Install Python 3.11+ and run: pip install -r requirements.txt"
  exit 1
fi

if ! "$PYTHON" -c "import uvicorn" >/dev/null 2>&1; then
  echo "uvicorn is not installed for $PYTHON. Run: $PYTHON -m pip install -r requirements.txt"
  exit 1
fi

export PYTHONPATH="$(pwd)"
exec "$PYTHON" -m uvicorn backend.main:app --port "${PORT:-8000}" "$@"
