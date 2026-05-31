"""Weave instrumentation, made safe for an offline / not-logged-in machine.

Narrative for the W&B judges: *we use Weave to trace and debug a crew whose
entire job is debugging production incidents — observability all the way down.*

Every agent op and the Commander are decorated with `@weave.op()` (see agents.py
/ orchestrator.py / llm.py). Those decorators work whether or not `weave.init`
ever succeeds — when Weave isn't initialized the functions simply run untraced.

The one thing we must avoid is `weave.init()` blocking the server on an
interactive "paste your API key" prompt. So we only initialize when a login
clearly exists (WANDB_API_KEY env var, or a ~/.netrc entry for api.wandb.ai).
"""
from __future__ import annotations

import os
from pathlib import Path

import weave

PROJECT = "war-room"
_initialized = False


def _has_wandb_login() -> bool:
    if os.environ.get("WANDB_API_KEY"):
        return True
    netrc = Path.home() / ".netrc"
    try:
        return netrc.exists() and "api.wandb.ai" in netrc.read_text()
    except OSError:
        return False


def init_weave() -> bool:
    """Initialize Weave if (and only if) we can do so without prompting.

    Returns True if traces will be logged, False if we're running untraced.
    Safe to call multiple times.
    """
    global _initialized
    if _initialized:
        return True
    if not _has_wandb_login():
        print(
            "[weave] No W&B login detected — running UNTRACED. "
            "Run `wandb login` (or set WANDB_API_KEY) then restart to log traces "
            "to the 'war-room' project. All @weave.op decorators stay active."
        )
        return False
    try:
        # Prefer the explicit team/project the user created (required by the
        # W&B Inference Service for usage tracking); else compose from entity.
        project = os.environ.get("WANDB_PROJECT")
        if not project:
            entity = os.environ.get("WANDB_ENTITY")
            project = f"{entity}/{PROJECT}" if entity else PROJECT
        weave.init(project)
        _initialized = True
        print(f"[weave] Initialized — tracing every agent op to project '{project}'.")
        return True
    except Exception as e:  # never let observability crash the harness
        print(f"[weave] init failed ({type(e).__name__}: {e}); running untraced.")
        return False
