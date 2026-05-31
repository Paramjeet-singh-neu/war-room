"""MCP-framed data-source access.

Each production data source is exposed as an MCP-style tool/resource: a named
capability with a typed input (the incident window) that returns a raw slice.
Investigators call ONLY their own source's tool — they never see the other
sources' raw data, and the Commander never calls these at all (it only ever
sees structured Findings). That's the clean separation of concerns.

In a full deployment these would be three MCP servers (a logs server, a metrics
server, a deploys server). Here they're in-process tools over the scenario
payload, but the boundary and contract are identical.
"""
from __future__ import annotations

import weave


class IncidentDataSource:
    """An MCP-style server bound to one incident's raw payload."""

    def __init__(self, scenario: dict):
        self._scenario = scenario

    @weave.op()
    def fetch_logs(self, window_start: str, window_end: str) -> str:
        """MCP tool: application/error logs for the incident window."""
        return self._scenario["logs"]

    @weave.op()
    def fetch_metrics(self, window_start: str, window_end: str) -> str:
        """MCP tool: system metrics (latency, error rate, CPU, memory)."""
        return self._scenario["metrics"]

    @weave.op()
    def fetch_deploys(self, window_start: str, window_end: str) -> str:
        """MCP tool: recent deploys, config changes and merges in the window."""
        return self._scenario["deploys"]

    def fetch(self, source: str, window_start: str, window_end: str) -> str:
        return {
            "logs": self.fetch_logs,
            "metrics": self.fetch_metrics,
            "deploys": self.fetch_deploys,
        }[source](window_start, window_end)
