"""CLI: demo | pull | serve | build | report | verify | canon | mcp."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .ckg import write_ckg
from .graph import build_graph
from .ingest import load_fixtures, pull, serve
from .report import full_report, markdown_report
from .store import EventStore, canonical_bytes

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVENTS = Path(os.environ.get("THREAD_OUTCOMES_EVENTS", ROOT / "data" / "events"))
DEFAULT_OUT = Path(os.environ.get("THREAD_OUTCOMES_OUT", ROOT / "data" / "graph"))
FIXTURES = ROOT / "fixtures" / "events"


def cmd_demo(args):
    store = EventStore(args.events)
    loaded = load_fixtures(store, FIXTURES)
    g = build_graph(store)
    paths = write_ckg(g, args.out)
    print(f"loaded {len(loaded)} fixture events into {args.events}")
    print(f"graph: {len(g.nodes)} nodes, {len(g.edges)} edges -> {paths['md']}\n")
    print(markdown_report(g))


def cmd_pull(args):
    store = EventStore(args.events)
    fetched = pull(store, args.webhook_id, max_events=args.max)
    print(f"stored {len(fetched)} new events ({len(store)} total)")


def cmd_serve(args):
    serve(EventStore(args.events), args.port, os.environ.get("RESEND_WEBHOOK_SECRET"))


def cmd_build(args):
    g = build_graph(EventStore(args.events))
    paths = write_ckg(g, args.out)
    print(f"{len(g.nodes)} nodes, {len(g.edges)} edges -> {paths['md']}")


def cmd_report(args):
    g = build_graph(EventStore(args.events))
    print(json.dumps(full_report(g), indent=2) if args.json else markdown_report(g))


def cmd_verify(args):
    g = build_graph(EventStore(args.events))
    node = g.nodes.get(args.node_id)
    if not node:
        sys.exit(f"unknown node {args.node_id}")
    from .mcp_server import _verify_command

    print(json.dumps({"node_id": node.id, "source_url": node.source_url, "source_hash": node.source_hash,
                      "verify": _verify_command(node.source_url)}, indent=2))


def cmd_canon(args):
    """stdin: a GET /webhooks/{id}/events/{eid} response -> canonical payload bytes."""
    doc = json.load(sys.stdin)
    payload = doc.get("payload", doc)
    sys.stdout.buffer.write(canonical_bytes(payload))


def cmd_mcp(args):
    from .mcp_server import main as mcp_main

    mcp_main(args.events)


def main(argv=None):
    p = argparse.ArgumentParser(prog="thread-outcomes")
    p.add_argument("--events", type=Path, default=DEFAULT_EVENTS, help="event store directory")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="graph output directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="load fixtures, build the graph, print the report").set_defaults(fn=cmd_demo)
    sp = sub.add_parser("pull", help="fetch a webhook's events via the Resend API")
    sp.add_argument("--webhook-id", required=True)
    sp.add_argument("--max", type=int, default=500)
    sp.set_defaults(fn=cmd_pull)
    sp = sub.add_parser("serve", help="receive webhooks over HTTP")
    sp.add_argument("--port", type=int, default=8787)
    sp.set_defaults(fn=cmd_serve)
    sub.add_parser("build", help="write the CKG from stored events").set_defaults(fn=cmd_build)
    sp = sub.add_parser("report", help="print what the inbox learned")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("verify", help="show the source behind a node")
    sp.add_argument("node_id")
    sp.set_defaults(fn=cmd_verify)
    sub.add_parser("canon", help="canonicalize an API event response for hashing").set_defaults(fn=cmd_canon)
    sub.add_parser("mcp", help="run the MCP server over stdio").set_defaults(fn=cmd_mcp)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
