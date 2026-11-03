#!/usr/bin/env bash
# Start the planner locally on macOS or Linux.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$root/.venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$root/.venv"
  "$root/.venv/bin/pip" install --upgrade pip
  "$root/.venv/bin/pip" install -r "$root/requirements-dev.txt"
fi

if [ ! -f "$root/.env" ]; then
  cp "$root/.env.example" "$root/.env"
  echo "Created .env - add your GOOGLE_API_KEY before planning a trip."
fi

cd "$root"
exec "$root/.venv/bin/uvicorn" app.main:app --reload --host 127.0.0.1 --port 8000
