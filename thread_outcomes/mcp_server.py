"""MCP server: the agent's read-side of the outcome graph.

Run with ``thread-outcomes mcp``; point an MCP client at it over stdio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .graph import Graph, build_graph
from .report import (
    agent_draft_outcomes,
    bounce_report,
    label_corrections,
    thread_summaries,
    unanswered_threads,
)
from .store import EventStore


def make_server(events_dir: Path) -> FastMCP:
    mcp = FastMCP("resend-thread-outcomes")
    state: dict[str, Graph] = {}

    def graph() -> Graph:
        # Rebuild on every call: the store is append-only and small.
        state["g"] = build_graph(EventStore(events_dir))
        return state["g"]

    @mcp.tool()
    def thread_outcomes(thread_id: str | None = None) -> list[dict[str, Any]]:
        """Per-thread outcomes: reply latency, delivery result, bounce detail, complaint,
        whether the customer replied, agent labels a person changed, agent draft status.
        Pass thread_id to get one thread, omit for all."""
        rows = thread_summaries(graph())
        return [r for r in rows if not thread_id or r["thread_id"] == thread_id]

    @mcp.tool()
    def bounces() -> dict[str, Any]:
        """Bounce rate and bounce reasons by recipient domain, over every outbound message."""
        return bounce_report(graph())

    @mcp.tool()
    def label_corrections_tool() -> list[dict[str, Any]]:
        """Labels the inbox agent applied that a person later removed or replaced.
        Each row cites the webhook event that recorded the correction."""
        return label_corrections(graph())

    @mcp.tool()
    def agent_drafts() -> dict[str, Any]:
        """How agent-created drafts ended: sent by a person, deleted, or still pending."""
        return agent_draft_outcomes(graph())

    @mcp.tool()
    def unanswered(older_than_hours: float = 4) -> list[dict[str, Any]]:
        """Inbound threads with no reply after the given number of hours."""
        return unanswered_threads(graph(), older_than_hours)

    @mcp.tool()
    def verify_source(node_id: str) -> dict[str, Any]:
        """Return the source URL and content hash behind a node, with the command that
        re-derives the hash. The hash is the trust anchor; the URL is a fetch hint."""
        g = graph()
        node = g.nodes.get(node_id)
        if node is None:
            return {"error": f"unknown node {node_id}", "known_types": sorted({n.type for n in g.nodes.values()})}
        return {
            "node_id": node.id,
            "type": node.type,
            "label": node.label,
            "source_url": node.source_url,
            "source_hash": node.source_hash,
            "verify": _verify_command(node.source_url),
        }

    return mcp


def _verify_command(source_url: str) -> str:
    if source_url.startswith("https://api.resend.com/"):
        return (
            f'curl -s -H "Authorization: Bearer $RESEND_API_KEY" {source_url} '
            "| thread-outcomes canon | sha256sum"
        )
    if source_url.startswith("fixture://"):
        return f"sha256sum fixtures/events/{source_url.removeprefix('fixture://')}"
    return "pushed event: compare against the raw body stored under data/events/"


def main(events_dir: Path) -> None:
    make_server(events_dir).run()
