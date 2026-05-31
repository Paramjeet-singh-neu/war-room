"""The Incident Commander — coordinator / harness.

Flow:
  1. Dispatch an A2A `Task` to all three investigators AT ONCE (asyncio.gather).
  2. Stream each investigator's start + structured `Finding` to the UI as it lands.
  3. Correlate the findings PROGRAMMATICALLY (correlation.rank_hypotheses).
  4. Synthesize a human narrative + adjudication on top of the ranking
     (gpt-4o, or a deterministic template when no API key).

The Commander never touches raw logs/metrics/deploys — only `Finding` objects.
Every step is a `@weave.op`, so the whole crew shows up as one trace tree.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

import weave

from backend.agents import investigate
from backend.correlation import detect_disagreement, rank_hypotheses
from backend.llm import COMMANDER_MODEL, chat_text, have_openai
from backend.schema import CommanderReport, Finding, Hypothesis, Source
from backend.tools import IncidentDataSource

Emit = Callable[[dict], Awaitable[None]]

INVESTIGATORS: list[Source] = ["logs", "metrics", "deploys"]

# Small staggered pacing so the "fire together, resolve at different times"
# beat is visible in the graph even in mock mode. Tunable / disable-able.
_PACING = os.environ.get("WARROOM_PACING", "1") != "0"
_STAGGER = {"logs": 0.7, "metrics": 1.3, "deploys": 1.0}


async def _run_one(source: Source, scenario: dict, ds: IncidentDataSource, emit: Emit) -> Finding:
    await emit({"type": "agent_start", "source": source})
    if _PACING:
        await asyncio.sleep(_STAGGER[source])
    finding = await investigate(source, scenario, ds)
    await emit({"type": "finding", "source": source, "finding": finding.model_dump()})
    return finding


@weave.op()
async def commander_synthesize(
    incident_start: str,
    ranked: list[Hypothesis],
    disagreement: dict | None,
) -> tuple[str, str | None]:
    """Write narrative + adjudication on top of the programmatic ranking."""
    top = ranked[0]
    if not have_openai():
        srcs = ", ".join(top.converging_sources)
        narrative = (
            f"Top hypothesis: {top.cause}. Converging sources: {srcs} "
            f"({len(top.converging_sources)} of 3), with timestamps aligned to the "
            f"incident start at {incident_start}. Suggested action: {top.suggested_action} "
            f"Aggregate confidence: {top.confidence}."
        )
        adjudication = None
        if disagreement:
            adjudication = (
                "Sources disagreed on the cause. Adjudication: the change "
                f"({top.cause}) immediately precedes the symptoms and explains them as a "
                "downstream effect, so it outranks the symptom-level hypotheses."
            )
        return narrative, adjudication

    ranked_lines = "\n".join(
        f"- {h.cause} (score {h.score}, sources={h.converging_sources}, "
        f"confidence={h.confidence}): {h.summary}"
        for h in ranked
    )
    system = (
        "You are the Incident Commander. You are given an ALREADY-RANKED list of "
        "root-cause hypotheses (ranked programmatically by source convergence and "
        "timestamp alignment). Do not re-rank. In 2-3 crisp sentences state the top "
        "hypothesis, why the correlation holds (which sources converge + the timing), "
        "and the suggested action. Be concrete and calm — this is a 3am page."
    )
    user = f"Incident start: {incident_start}\nRanked hypotheses:\n{ranked_lines}"
    narrative = await chat_text(COMMANDER_MODEL, system, user)

    adjudication = None
    if disagreement:
        adj_system = (
            "Two investigators disagreed on the root cause. Briefly adjudicate (2 "
            "sentences): explain why the winning hypothesis outranks the other, using "
            "the timing (which event preceded which) and cause-vs-symptom reasoning."
        )
        adj_user = (
            f"Disagreement (cause -> sources): {disagreement}\n"
            f"Winning hypothesis: {ranked[0].cause}\n"
            f"Runner-up: {ranked[1].cause if len(ranked) > 1 else 'n/a'}"
        )
        adjudication = await chat_text(COMMANDER_MODEL, adj_system, adj_user)

    return narrative, adjudication


@weave.op()
async def run_incident(scenario: dict, emit: Emit) -> CommanderReport:
    """Dispatch the alert to all investigators in parallel and synthesize a report."""
    ds = IncidentDataSource(scenario)
    incident_start = scenario["incident_start"]

    await emit(
        {
            "type": "incident",
            "scenario": scenario["id"],
            "title": scenario["title"],
            "incident_start": incident_start,
            "alert": scenario["alert"],
        }
    )
    # A2A fan-out: every investigator is dispatched at the same instant.
    await emit({"type": "dispatch", "agents": INVESTIGATORS})

    findings: list[Finding] = await asyncio.gather(
        *(_run_one(s, scenario, ds, emit) for s in INVESTIGATORS)
    )

    # Programmatic correlation — not text concatenation.
    ranked = rank_hypotheses(findings, incident_start)
    disagreement = detect_disagreement(findings)
    await emit(
        {
            "type": "correlate",
            "ranked": [h.model_dump() for h in ranked],
            "disagreement": disagreement,
            "top_cause": ranked[0].cause,
        }
    )

    narrative, adjudication = await commander_synthesize(incident_start, ranked, disagreement)
    report = CommanderReport(
        incident_id=scenario["id"],
        incident_start=incident_start,
        narrative=narrative,
        ranked=ranked,
        adjudication=adjudication,
    )
    await emit({"type": "hypothesis", "report": report.model_dump()})
    await emit({"type": "done"})
    return report
