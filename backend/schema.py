"""The contract that makes programmatic correlation possible.

Two message types model the A2A-style handoff between the Incident Commander
and the specialist investigators:

  * `Task`    — Commander -> investigator. A structured assignment ("investigate
                this source for this incident window"). The investigator only
                ever sees its own slice of raw data (clean separation of concerns).
  * `Finding` — investigator -> Commander. The EXACT structured payload the
                Commander correlates on. The Commander never sees raw logs /
                metrics / deploys — only these findings.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["logs", "metrics", "deploys"]
Severity = Literal["low", "medium", "high", "critical"]


class Task(BaseModel):
    """A2A task: Commander -> investigator."""

    source: Source
    incident_id: str
    window_start: str  # ISO-8601
    window_end: str  # ISO-8601
    instruction: str


class Finding(BaseModel):
    """Structured finding: investigator -> Commander.

    This is the locked schema from the build spec. The Commander ranks
    hypotheses purely from these fields — no text concatenation.
    """

    source: Source
    finding: str = Field(description="Human-readable description of what was observed")
    timestamp: str = Field(description="ISO-8601 of the key observed event")
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    points_to: str = Field(
        description="Short id/string of the suspected cause, e.g. 'deploy-4471'"
    )


class Hypothesis(BaseModel):
    """One ranked root-cause hypothesis emitted by the Commander."""

    cause: str
    summary: str
    suggested_action: str
    confidence: Severity  # low | medium | high | critical, reused as a band label
    score: float
    converging_sources: list[Source]
    aligned_timestamps: list[str]


class CommanderReport(BaseModel):
    incident_id: str
    incident_start: str
    narrative: str
    ranked: list[Hypothesis]
    adjudication: str | None = None
