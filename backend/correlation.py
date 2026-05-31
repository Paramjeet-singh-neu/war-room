"""Programmatic correlation — the architectural heart.

The Commander does NOT concatenate the investigators' prose. It correlates the
structured `Finding` fields:

  (a) convergence  — how many distinct sources' `points_to` name the same cause
  (b) alignment    — how tightly each finding's `timestamp` sits around the
                     incident start (a *change* that immediately precedes the
                     symptoms scores higher than the symptoms themselves)

It returns a ranked list of `Hypothesis` objects. An LLM (or a template) only
ever writes narrative *on top of* this ranking — it never produces the ranking.
"""
from __future__ import annotations

from datetime import datetime

from backend.schema import Finding, Hypothesis, Severity

_SEV_WEIGHT = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
_ALIGN_WINDOW_S = 300.0  # findings within 5 min of incident start are "aligned"


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _alignment(event_ts: str, incident_start: str) -> float:
    """1.0 when the event coincides with the incident start, decaying to 0 at the window edge."""
    try:
        gap = abs((_parse_ts(event_ts) - _parse_ts(incident_start)).total_seconds())
    except ValueError:
        return 0.0
    return max(0.0, 1.0 - gap / _ALIGN_WINDOW_S)


def _is_change(cause: str) -> bool:
    return cause.startswith(("deploy-", "config-", "pr-", "release-"))


def _band(score: float) -> Severity:
    if score >= 6.0:
        return "critical"
    if score >= 4.5:
        return "high"
    if score >= 3.0:
        return "medium"
    return "low"


def _suggested_action(cause: str, findings: list[Finding]) -> str:
    if cause.startswith(("deploy-", "release-")):
        return f"Roll back {cause} immediately, then verify error rate recovers."
    if cause.startswith(("config-", "pr-")):
        return f"Revert {cause} and redeploy the previous known-good config."
    if "connection-pool" in cause or "pool" in cause:
        return (
            f"Restore the connection-pool size to its prior value (a recent deploy "
            f"likely shrank it); if it was organic load, scale the pool / DB."
        )
    return f"Mitigate {cause}: page the owning team and prepare a rollback of the nearest change."


def rank_hypotheses(findings: list[Finding], incident_start: str) -> list[Hypothesis]:
    """Cluster findings by `points_to` and score each candidate cause."""
    causes: dict[str, list[Finding]] = {}
    for f in findings:
        causes.setdefault(f.points_to, []).append(f)

    hypotheses: list[Hypothesis] = []
    for cause, group in causes.items():
        sources = sorted({f.source for f in group})
        convergence = len(sources)  # distinct sources voting for this cause

        # Alignment: use the tightest-aligned finding in the group.
        aligns = [_alignment(f.timestamp, incident_start) for f in group]
        best_align = max(aligns) if aligns else 0.0

        avg_conf = sum(f.confidence for f in group) / len(group)
        max_sev = max(_SEV_WEIGHT[f.severity] for f in group)
        change_bonus = 1.0 if _is_change(cause) else 0.0

        # Score: convergence dominates, then temporal alignment, then the agents'
        # own confidence/severity, with a nudge for "this was an actual change".
        score = (
            2.0 * convergence
            + 2.0 * best_align
            + 1.0 * avg_conf
            + 1.0 * max_sev
            + change_bonus
        )

        summary = "; ".join(f"[{f.source}] {f.finding}" for f in group)
        hypotheses.append(
            Hypothesis(
                cause=cause,
                summary=summary,
                suggested_action=_suggested_action(cause, group),
                confidence=_band(score),
                score=round(score, 3),
                converging_sources=sources,
                aligned_timestamps=[f.timestamp for f in group],
            )
        )

    hypotheses.sort(key=lambda h: h.score, reverse=True)
    return hypotheses


def detect_disagreement(findings: list[Finding]) -> dict | None:
    """If investigators point at different causes, describe the split for adjudication."""
    by_cause: dict[str, list[str]] = {}
    for f in findings:
        by_cause.setdefault(f.points_to, []).append(f.source)
    if len(by_cause) <= 1:
        return None
    return {cause: srcs for cause, srcs in by_cause.items()}
