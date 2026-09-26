#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
echo "Mnemo: http://127.0.0.1:8765"
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
