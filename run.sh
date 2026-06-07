#!/usr/bin/env bash
# War Room — launch script.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

# Optional: set WANDB_API_KEY + WANDB_PROJECT for W&B Inference and Weave tracing.
# Alternative: set OPENAI_API_KEY to use OpenAI models (see .env.example).
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 "$@"
