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

from backend.llm import commander_model, have_llm, investigator_model, parse_finding
from backend.schema import Finding, Source, Task
from backend.tools import IncidentDataSource

# Constrain points_to to a small shared vocabulary so the Commander's
# convergence count is meaningful across sources.
_VOCAB_HINT = (
    "Set `points_to` to a short, stable cause id. Prefer a deploy/config id "
    "(e.g. 'deploy-4471', 'config-993') ONLY if that change plausibly explains the "
    "symptoms you see; otherwise name the actual technical mechanism you observe "
    "(e.g. 'upstream-stripe-outage', 'dns-resolution-failure', 'redis-memory-eviction', "
    "'disk-full', 'db-connection-pool').\n"
    "Do NOT blame a deploy/config marker just because it appears near the onset: a "
    "change that only touches email copy, UI text, logging, labels, retry counts, or "
    "docs almost never causes 5xx / DB / latency / connectivity / DNS / cert failures. "
    "In that case point_to the real cause in your slice, not the deploy.\n"
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
        "changed right before this started?'.\n"
        "CRITICAL: do not blame a change just because it is recent. Only point_to a "
        "change if BOTH (a) its timing precedes the incident onset AND (b) its diff "
        "plausibly explains the symptoms. A change that only touches email copy, UI "
        "text, logging, docs, labels, or metrics is almost never the cause of 5xx/DB/"
        "latency/connectivity incidents — call it out and rule it OUT. If the only "
        "recent change is unrelated, or there is NO change inside the window, set "
        "points_to to 'no-recent-change', severity low, confidence <= 0.3, and state "
        "what you ruled out. When a change IS plausible, explain the causal link."
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
    for f in scenario.get("mock_findings", []):
        if f.source == source:
            return f.model_copy(deep=True)
    # Hard eval scenarios are scored live and carry no curated findings; return a
    # neutral placeholder so mock mode doesn't crash (it will simply score poorly).
    return Finding(
        source=source,
        finding=f"(mock mode: no curated {source} finding for '{scenario['id']}')",
        timestamp=scenario["incident_start"],
        severity="low",
        confidence=0.1,
        points_to="unknown",
    )


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
    if not raw or not raw.strip():  # custom incident with this source left blank
        return Finding(source=source, finding=f"No {source} data provided.",
                       timestamp=scenario["incident_start"], severity="low",
                       confidence=0.0, points_to="no-data")
    if have_llm():
        return await _llm_finding(source, scenario, raw)
    return _mock_finding(source, scenario)


_ADJUDICATOR_ROLE = (
    "You are an Adjudicator — a focused specialist the Incident Commander spins up "
    "ONLY when round-1 investigators disagree. Round 1 surfaced two competing causes. "
    "Your job: decide the TRUE root cause vs the downstream symptom. A change (deploy/"
    "config) that PRECEDED the incident and whose diff plausibly produces the observed "
    "symptom is the root cause; the symptom is its effect. If the change is unrelated "
    "(copy/UI/logging) or post-dates onset, the other hypothesis stands. Examine the "
    "evidence below and return ONE Finding whose `points_to` is the true root cause."
)


@weave.op()
async def investigate_specialist(
    leading: str,
    dissent: str,
    leading_sources: list[str],
    dissent_sources: list[str],
    scenario: dict,
    datasource: IncidentDataSource,
) -> Finding:
    """Dynamic second-round adjudicator: resolve a contested round-1 hypothesis."""
    # The adjudicator deep-dives the change/deploy evidence to test causality.
    deploys = datasource.fetch("deploys", scenario["window_start"], scenario["window_end"])
    if not have_llm():
        # Deterministic: a plausible preceding change wins; symptom is downstream.
        root = leading if _looks_like_change(leading) else dissent
        symptom = dissent if root == leading else leading
        return Finding(
            source="adjudicator",
            finding=(f"Deep-dive: {root} is the root cause; '{symptom}' is a downstream "
                     f"symptom it explains. Conflict resolved in favor of {root}."),
            timestamp=scenario["incident_start"],
            severity="high",
            confidence=0.9,
            points_to=root,
        )

    system = _ADJUDICATOR_ROLE + "\n\n" + _SCHEMA_SPEC.replace("{SOURCE}", "adjudicator")
    user = (
        f"Incident: {scenario['title']} (onset ~{scenario['incident_start']}).\n"
        f"Hypothesis A: '{leading}' (from {leading_sources}).\n"
        f"Hypothesis B: '{dissent}' (from {dissent_sources}).\n\n"
        f"--- CHANGE/DEPLOY EVIDENCE ---\n{deploys}\n--- END ---\n\n"
        "Decide the true root cause and return one Finding as JSON. Set `points_to` to "
        "the root cause (prefer the change if it plausibly explains the symptom)."
    )
    finding = await parse_finding(commander_model(), system, user, Finding)
    finding.source = "adjudicator"
    return finding


def _looks_like_change(cause: str) -> bool:
    return cause.startswith(("deploy-", "config-", "pr-", "release-"))
