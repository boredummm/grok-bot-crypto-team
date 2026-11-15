#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
python3 - <<'PY'
from pathlib import Path
import secrets
path = Path(".env")
text = path.read_text()
for name in ("SOLSWARM_ADMIN_TOKEN", "SOLSWARM_MONITOR_TOKEN"):
    lines = []
    found = False
    for line in text.splitlines():
        if line.startswith(name + "="):
            found = True
            current = line.split("=", 1)[1].strip()
            if not current:
                line = f"{name}={secrets.token_hex(32)}"
            lines.append(line)
        else:
            lines.append(line)
    if not found:
        lines.append(f"{name}={secrets.token_hex(32)}")
    text = "\n".join(lines) + "\n"
path.write_text(text)
print("Tokens are in .env. They are not printed.")
PY
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -r backend/requirements.txt
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi
(cd backend && ../.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000) &
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173) &
wait
