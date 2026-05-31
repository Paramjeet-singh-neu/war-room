"""The three specialist investigators.

Each investigator:
  * receives an A2A `Task` from the Commander,
  * pulls ONLY its own raw slice via the MCP-style data-source tool,
  * reasons over that slice (gpt-4o-mini, or a deterministic mock),
  * returns the locked structured `Finding`.

They share no state and never read each other's data. They run concurrently
(see orchestrator.asyncio.gather).
"""
from __future__ import annotations

import weave

from backend.llm import have_llm, investigator_model, parse_finding
from backend.schema import Finding, Source, Task
from backend.tools import IncidentDataSource

# Constrain points_to to a small shared vocabulary so the Commander's
# convergence count is meaningful across sources.
_VOCAB_HINT = (
    "Set `points_to` to a short, stable cause id. Prefer a deploy/config id "
    "(e.g. 'deploy-4471', 'config-993') if a change plausibly explains the "
    "onset; otherwise an infra/component id (e.g. 'db-connection-pool'). "
    "Use lowercase-hyphenated ids and reuse the SAME id another agent would pick."
)

_ROLE = {
    "logs": (
        "You are the Logs investigator on an incident-response crew. You scan "
        "application/error logs for the incident window and surface NEW error "
        "signatures, spikes, and stack traces. Identify the first/key error event "
        "and the most likely cause."
    ),
    "metrics": (
        "You are the Metrics investigator on an incident-response crew. You analyze "
        "system metrics (latency, error rate, CPU, memory). Identify WHAT degraded "
        "and exactly WHEN, and whether it looks like a code fault vs resource "
        "saturation. Note any deploy markers near the degradation."
    ),
    "deploys": (
        "You are the Deploys investigator on an incident-response crew. You review "
        "recent deploys, config changes and merges in the window and answer 'what "
        "changed right before this started?'. Tie any suspicious change to the "
        "incident onset time."
    ),
}


def _build_task(source: Source, scenario: dict) -> Task:
    return Task(
        source=source,
        incident_id=scenario["id"],
        window_start=scenario["window_start"],
        window_end=scenario["window_end"],
        instruction=(
            f"Incident: {scenario['title']}. Alert fired ~{scenario['incident_start']}. "
            f"Investigate the {source} for the window and return a single structured finding."
        ),
    )


def _mock_finding(source: Source, scenario: dict) -> Finding:
    for f in scenario["mock_findings"]:
        if f.source == source:
            return f.model_copy(deep=True)
    raise KeyError(f"no mock finding for {source} in scenario {scenario['id']}")


_SCHEMA_SPEC = (
    'Respond with ONLY a single JSON object, no prose, matching exactly:\n'
    '{\n'
    f'  "source": "{{SOURCE}}",\n'
    '  "finding": "specific, concise description of what you observed",\n'
    '  "timestamp": "ISO-8601 of the key observed event, e.g. 2024-11-12T14:32:05Z",\n'
    '  "severity": "low | medium | high | critical",\n'
    '  "confidence": 0.0,   // a number in [0,1]\n'
    '  "points_to": "short cause id, e.g. deploy-4471 or db-connection-pool"\n'
    '}'
)


async def _llm_finding(source: Source, scenario: dict, raw: str) -> Finding:
    system = _ROLE[source] + "\n\n" + _VOCAB_HINT + "\n\n" + _SCHEMA_SPEC.replace("{SOURCE}", source)
    user = (
        f"Incident window: {scenario['window_start']} .. {scenario['window_end']}\n"
        f"Alert: {scenario['alert']}\n\n"
        f"--- RAW {source.upper()} (your slice only) ---\n{raw}\n"
        f"--- END ---\n\n"
        "Return one Finding as JSON."
    )
    finding = await parse_finding(investigator_model(), system, user, Finding)
    finding.source = source  # authoritative; never trust the model for this
    return finding


@weave.op()
async def investigate(source: Source, scenario: dict, datasource: IncidentDataSource) -> Finding:
    """Run one investigator end-to-end: task -> MCP fetch -> finding."""
    task = _build_task(source, scenario)
    raw = datasource.fetch(source, task.window_start, task.window_end)  # MCP tool call
    if have_llm():
        return await _llm_finding(source, scenario, raw)
    return _mock_finding(source, scenario)
