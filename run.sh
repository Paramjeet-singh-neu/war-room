#!/usr/bin/env bash
# War Room — launch script.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

# Optional: log traces to Weave. Run `wandb login` once (or set WANDB_API_KEY).
# Optional: real LLM reasoning needs OPENAI_API_KEY (see .env.example).
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 "$@"
