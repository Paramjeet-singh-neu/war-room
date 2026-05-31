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

import json
import urllib.request

import weave


@weave.op()
def fetch_github_changes(repo: str, limit: int = 15) -> str:
    """MCP-style tool: pull REAL recent commits/merges from a public GitHub repo.

    This makes the Deploys investigator work on genuine, unseen change history
    instead of a canned slice. Uses the public GitHub API (no auth needed for
    public repos). `repo` is "owner/name". Fails soft with a readable message.
    """
    url = f"https://api.github.com/repos/{repo}/commits?per_page={limit}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "war-room"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            commits = json.load(r)
    except Exception as e:  # noqa: BLE001
        return f"(could not fetch GitHub changes for {repo}: {type(e).__name__}: {e})"
    if not isinstance(commits, list) or not commits:
        return f"(no commits found for {repo})"
    lines = [f"recent changes (REAL, from github.com/{repo}), most recent first:"]
    for c in commits:
        sha = c.get("sha", "")[:7]
        commit = c.get("commit", {})
        when = commit.get("author", {}).get("date", "?")
        author = commit.get("author", {}).get("name", "?")
        msg = (commit.get("message", "") or "").splitlines()[0][:100]
        lines.append(f"- {sha}  {when}  author={author}  \"{msg}\"")
    return "\n".join(lines)


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
