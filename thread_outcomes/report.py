"""The questions an agent asks of the graph, answered as plain data."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from .graph import Graph, Node, parse_ts

HUMAN_SOURCES = ("DASHBOARD", "API")


def _messages(g: Graph, thread: Node) -> list[Node]:
    ids = [e.dst for e in g.out(thread.id, "HAS_MESSAGE")]
    msgs = [g.nodes[i] for i in dict.fromkeys(ids)]
    return sorted(msgs, key=lambda m: m.props["received_at"])


def _outcomes(g: Graph, message: Node) -> list[Node]:
    return sorted((g.nodes[e.dst] for e in g.out(message.id, "RESULTED_IN")), key=lambda o: o.props["at"])


def thread_summary(g: Graph, thread: Node) -> dict[str, Any]:
    msgs = _messages(g, thread)
    inbound = [m for m in msgs if m.props["direction"] == "inbound"]
    outbound = [m for m in msgs if m.props["direction"] == "outbound"]
    first_in = inbound[0].props["received_at"] if inbound else None
    first_out = next((m.props["received_at"] for m in outbound if not first_in or m.props["received_at"] >= first_in), None)
    latency = None
    if first_in and first_out:
        latency = round((parse_ts(first_out) - parse_ts(first_in)).total_seconds())
    last_outcome = None
    bounce = None
    complained = False
    for m in outbound:
        for o in _outcomes(g, m):
            last_outcome = o.props["kind"]
            if o.props["kind"] == "bounced":
                bounce = {k: o.props.get(k) for k in ("type", "subType", "message") if o.props.get(k)}
            if o.props["kind"] == "complained":
                complained = True
    agent_labels = [g.nodes[e.dst].label for e in g.out(thread.id, "LABELED_BY_AGENT")]
    human_removed = [g.nodes[e.dst].label for e in g.edges if e.src == thread.id and e.rel in (f"UNLABELED_BY_{s}" for s in HUMAN_SOURCES)]
    drafts = []
    for e in g.out(thread.id, "DRAFTED_BY_AGENT"):
        d = g.nodes[e.dst]
        drafts.append(d.props["status"])
    customer_replied = any(m.props["received_at"] > first_out for m in inbound) if first_out else False
    return {
        "thread_id": thread.props["thread_id"],
        "subject": thread.label,
        "folder": thread.props.get("folder"),
        "labels": thread.props.get("labels", []),
        "assignee": thread.props.get("assignee"),
        "messages": len(msgs),
        "first_inbound_at": first_in,
        "first_outbound_at": first_out,
        "reply_latency_s": latency,
        "last_outcome": last_outcome,
        "bounce": bounce,
        "complained": complained,
        "customer_replied_after_us": customer_replied,
        "agent_labels": agent_labels,
        "agent_labels_removed_by_human": [l for l in agent_labels if l in human_removed],
        "agent_drafts": drafts,
        "source_url": thread.source_url,
        "source_hash": thread.source_hash,
    }


def thread_summaries(g: Graph) -> list[dict[str, Any]]:
    return [thread_summary(g, t) for t in sorted(g.nodes_of("Thread"), key=lambda t: t.props["thread_id"])]


def bounce_report(g: Graph) -> dict[str, Any]:
    sent: Counter[str] = Counter()
    bounced: Counter[str] = Counter()
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    uncorrelated = 0
    for m in g.nodes_of("Message"):
        if m.props["direction"] != "outbound":
            continue
        doms = {e.dst.split(":", 1)[1] for e in g.out(m.id, "SENT_TO")}
        for dom in doms:
            sent[dom] += 1
        for o in _outcomes(g, m):
            if o.props["kind"] == "bounced":
                for dom in doms:
                    bounced[dom] += 1
                    reasons[dom][f"{o.props.get('type')}/{o.props.get('subType')}"] += 1
    for o in g.nodes_of("Outcome"):
        if not o.props.get("correlated"):
            uncorrelated += 1
    domains = []
    for dom in sorted(sent, key=lambda d: (-bounced[d] / sent[d], d)):
        domains.append({
            "domain": dom,
            "sent": sent[dom],
            "bounced": bounced[dom],
            "bounce_rate": round(bounced[dom] / sent[dom], 3),
            "reasons": dict(reasons[dom]),
        })
    return {"domains": domains, "uncorrelated_outcomes": uncorrelated}


def label_corrections(g: Graph) -> list[dict[str, Any]]:
    """Labels the agent applied that a person later removed or replaced."""
    out = []
    for t in g.nodes_of("Thread"):
        agent_added = {e.dst: e for e in g.out(t.id, "LABELED_BY_AGENT")}
        if not agent_added:
            continue
        for e in g.out(t.id):
            if e.rel in (f"UNLABELED_BY_{s}" for s in HUMAN_SOURCES) and e.dst in agent_added and e.at >= agent_added[e.dst].at:
                replaced = [g.nodes[x.dst].label for x in g.out(t.id) if x.rel in (f"LABELED_BY_{s}" for s in HUMAN_SOURCES) and x.at >= e.at]
                out.append({
                    "thread_id": t.props["thread_id"],
                    "subject": t.label,
                    "agent_label": g.nodes[e.dst].label,
                    "removed_by": e.rel.split("_BY_")[1].lower(),
                    "human_label_now": replaced,
                    "source_url": e.source_url,
                    "source_hash": e.source_hash,
                })
    return out


def agent_draft_outcomes(g: Graph) -> dict[str, Any]:
    c: Counter[str] = Counter()
    for d in g.nodes_of("Draft"):
        if d.props.get("created_by") == "agent":
            c[d.props["status"]] += 1
    return dict(c)


def unanswered_threads(g: Graph, older_than_hours: float = 4) -> list[dict[str, Any]]:
    cutoff = parse_ts(g.as_of) - timedelta(hours=older_than_hours)
    out = []
    for s in thread_summaries(g):
        if s["first_inbound_at"] and not s["first_outbound_at"] and s["folder"] not in ("spam", "trash", "archive"):
            if parse_ts(s["first_inbound_at"]) <= cutoff:
                out.append({k: s[k] for k in ("thread_id", "subject", "first_inbound_at", "labels", "assignee")})
    return out


def full_report(g: Graph) -> dict[str, Any]:
    return {
        "as_of": g.as_of,
        "threads": thread_summaries(g),
        "bounces": bounce_report(g),
        "label_corrections": label_corrections(g),
        "agent_drafts": agent_draft_outcomes(g),
        "unanswered": unanswered_threads(g),
    }


def _fmt_latency(s: int | None) -> str:
    if s is None:
        return "-"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s / 3600:.1f}h"


def markdown_report(g: Graph) -> str:
    r = full_report(g)
    lines = [f"# What the inbox learned (as of {r['as_of']})", ""]
    lines += ["## Threads", "", "| thread | subject | reply latency | last outcome | customer replied | notes |", "|---|---|---|---|---|---|"]
    for t in r["threads"]:
        notes = []
        if t["bounce"]:
            notes.append(f"bounce {t['bounce'].get('type')}/{t['bounce'].get('subType')}")
        if t["complained"]:
            notes.append("complaint")
        if t["agent_labels_removed_by_human"]:
            notes.append("label corrected: " + ", ".join(t["agent_labels_removed_by_human"]))
        if t["agent_drafts"]:
            notes.append("agent draft " + "/".join(t["agent_drafts"]))
        if t["folder"] == "spam":
            notes.append("moved to spam")
        lines.append(
            f"| {t['thread_id'][:8]} | {t['subject']} | {_fmt_latency(t['reply_latency_s'])} | {t['last_outcome'] or '-'} | "
            f"{'yes' if t['customer_replied_after_us'] else 'no'} | {'; '.join(notes) or '-'} |"
        )
    lines += ["", "## Bounces by recipient domain", "", "| domain | sent | bounced | rate | reasons |", "|---|---|---|---|---|"]
    for d in r["bounces"]["domains"]:
        lines.append(f"| {d['domain']} | {d['sent']} | {d['bounced']} | {d['bounce_rate']:.0%} | {', '.join(f'{k} ×{v}' for k, v in d['reasons'].items()) or '-'} |")
    if r["bounces"]["uncorrelated_outcomes"]:
        lines.append(f"\nUncorrelated outcome events (no matching thread message): {r['bounces']['uncorrelated_outcomes']}")
    lines += ["", "## Agent labels a person changed", ""]
    if r["label_corrections"]:
        for c in r["label_corrections"]:
            lines.append(f"- {c['subject']}: agent said **{c['agent_label']}**, {c['removed_by']} replaced with {', '.join(c['human_label_now']) or 'nothing'}")
    else:
        lines.append("- none")
    lines += ["", "## Agent drafts", "", ", ".join(f"{k}: {v}" for k, v in r["agent_drafts"].items()) or "none"]
    lines += ["", "## Unanswered for 4h+", ""]
    lines += [f"- {u['subject']} (since {u['first_inbound_at']})" for u in r["unanswered"]] or ["- none"]
    lines += ["", "Every row traces to a stored webhook event; run `thread-outcomes verify <node-id>` for the bytes behind it.", ""]
    return "\n".join(lines)
