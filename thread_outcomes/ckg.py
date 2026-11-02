"""Write the graph as a Compact Knowledge Graph: CSV nodes/edges plus a .md."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import __version__
from .graph import Graph
from .report import markdown_report


def write_ckg(g: Graph, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = out_dir / "nodes.csv"
    edges_path = out_dir / "edges.csv"
    md_path = out_dir / "resend-thread-outcomes.md"

    with nodes_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "type", "label", "props_json", "source_url", "source_hash"])
        for n in g.nodes.values():
            w.writerow([n.id, n.type, n.label, json.dumps(n.props, sort_keys=True), n.source_url, n.source_hash])

    with edges_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["src", "rel", "dst", "at", "source_url", "source_hash"])
        for e in g.edges:
            w.writerow([e.src, e.rel, e.dst, e.at, e.source_url, e.source_hash])

    md_path.write_text(_markdown(g))
    return {"nodes": nodes_path, "edges": edges_path, "md": md_path}


def _markdown(g: Graph) -> str:
    types = {}
    for n in g.nodes.values():
        types[n.type] = types.get(n.type, 0) + 1
    rels = {}
    for e in g.edges:
        rels[e.rel] = rels.get(e.rel, 0) + 1
    sources = {}
    for n in g.nodes.values():
        sources.setdefault(n.source_url, n.source_hash)

    lines = [
        "# resend-thread-outcomes CKG",
        "",
        f"version: {__version__}",
        f"as_of: {g.as_of}",
        f"nodes: {len(g.nodes)}",
        f"edges: {len(g.edges)}",
        "",
        "## Node types",
        "",
        *[f"- {t}: {c}" for t, c in sorted(types.items())],
        "",
        "## Edge types",
        "",
        *[f"- {r}: {c}" for r, c in sorted(rels.items())],
        "",
        "## Sources",
        "",
        "| source_url | source_hash |",
        "|---|---|",
        *[f"| {u} | {h} |" for u, h in sorted(sources.items())],
        "",
        "## Edges",
        "",
        *[f"- {e.src} -[{e.rel}]-> {e.dst} @ {e.at} ({e.source_hash[:19]}…)" for e in g.edges],
        "",
        markdown_report(g).replace("# What the inbox learned", "## What the inbox learned", 1),
        "## EVAL",
        "benchmark: ckg-benchmark v0.6.2",
        "dataset: huggingface.co/datasets/danyarm/ckg-benchmark",
        "benchmarked: false",
        "rag_baseline_f1: 0.123",
        "graphrag_baseline_f1: 0.120",
        "mean_tokens: 269",
        "paper: github.com/Yarmoluk/ckg-benchmark/blob/main/paper/main.pdf",
        "",
    ]
    return "\n".join(lines)
