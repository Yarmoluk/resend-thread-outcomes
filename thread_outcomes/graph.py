"""Build the outcome graph from stored events.

Nodes: Inbox, Thread, Message, Outcome, Label, Recipient (a domain), Draft.
Every node and edge names the stored event it came from, so ``verify_source``
can hand back the bytes behind any claim the agent reads.

Edge types are domain-specific rather than the concept-graph vocabulary
(REQUIRES / ENABLES / ...), because these are facts about mail, not concepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .store import EventStore, StoredEvent

OUTCOME_EVENTS = {
    "email.delivered": "delivered",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.delivery_delayed": "delayed",
    "email.failed": "failed",
    "email.opened": "opened",
    "email.clicked": "clicked",
}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def address_of(value: str) -> str:
    """``Ada <ada@example.org>`` -> ``ada@example.org``."""
    if "<" in value and value.endswith(">"):
        return value[value.rindex("<") + 1 : -1].strip().lower()
    return value.strip().lower()


def domain_of(address: str) -> str:
    return address_of(address).rsplit("@", 1)[-1]


@dataclass
class Node:
    id: str
    type: str
    label: str
    props: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    source_hash: str = ""


@dataclass
class Edge:
    src: str
    rel: str
    dst: str
    source_url: str
    source_hash: str
    at: str


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    as_of: str = ""

    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        # Keep the first provenance, merge newer facts.
        existing.props.update(node.props)
        return existing

    def add_edge(self, src: str, rel: str, dst: str, ev: StoredEvent) -> None:
        self.edges.append(Edge(src, rel, dst, ev.source_url, ev.source_hash, ev.created_at))

    def nodes_of(self, type_: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == type_]

    def out(self, src: str, rel: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.src == src and (rel is None or e.rel == rel)]

    def inbound(self, dst: str, rel: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.dst == dst and (rel is None or e.rel == rel)]


def _node_from_event(ev: StoredEvent, id_: str, type_: str, label: str, **props) -> Node:
    return Node(id_, type_, label, props, ev.source_url, ev.source_hash)


def _message_node(g: Graph, ev: StoredEvent, thread_id: str, email: dict[str, Any]) -> Node:
    node = g.add_node(
        _node_from_event(
            ev,
            f"message:{email['id']}",
            "Message",
            email.get("subject") or "(no subject)",
            email_id=email["id"],
            thread_id=thread_id,
            direction=email["direction"],
            sender=address_of(email["from"]),
            recipients=[address_of(a) for a in email.get("to", [])],
            message_id=email.get("message_id"),
            received_at=email["received_at"],
        )
    )
    g.add_edge(f"thread:{thread_id}", "HAS_MESSAGE", node.id, ev)
    if email["direction"] == "outbound":
        for addr in email.get("to", []):
            dom = domain_of(addr)
            g.add_node(Node(f"recipient:{dom}", "Recipient", dom, {"domain": dom}, ev.source_url, ev.source_hash))
            g.add_edge(node.id, "SENT_TO", f"recipient:{dom}", ev)
    return node


def _thread_node(g: Graph, ev: StoredEvent, thread: dict[str, Any], inbox_id: str) -> Node:
    node = g.add_node(
        _node_from_event(
            ev,
            f"thread:{thread['id']}",
            "Thread",
            thread.get("subject") or "(no subject)",
            thread_id=thread["id"],
            inbox_id=inbox_id,
            folder=thread.get("folder"),
            labels=[lbl["name"] for lbl in thread.get("labels", [])],
            read=thread.get("read"),
        )
    )
    g.add_node(Node(f"inbox:{inbox_id}", "Inbox", inbox_id, {"inbox_id": inbox_id}, ev.source_url, ev.source_hash))
    if not g.inbound(node.id, "HAS_THREAD"):
        g.add_edge(f"inbox:{inbox_id}", "HAS_THREAD", node.id, ev)
    return node


def build_graph(store: EventStore) -> Graph:
    events = sorted(store, key=lambda e: e.created_at)
    g = Graph(as_of=events[-1].created_at if events else "")
    by_email_id: dict[str, str] = {}
    by_message_id: dict[str, str] = {}

    for ev in events:
        d = ev.data
        t = ev.type

        if t in ("inbox.thread.created", "inbox.email.received", "inbox.email.sent"):
            thread = _thread_node(g, ev, d["thread"], d["inbox_id"])
            msg = _message_node(g, ev, thread.props["thread_id"], d["email"])
            by_email_id[msg.props["email_id"]] = msg.id
            if msg.props["message_id"]:
                by_message_id[msg.props["message_id"]] = msg.id

        elif t == "inbox.thread.folder.updated":
            thread = _thread_node(g, ev, d["thread"], d["inbox_id"])
            thread.props["folder"] = d["to"]
            fid = f"folder:{d['to']}"
            g.add_node(Node(fid, "Folder", d["to"], {}, ev.source_url, ev.source_hash))
            g.add_edge(thread.id, f"MOVED_TO_BY_{d['source'].upper()}", fid, ev)

        elif t == "inbox.thread.labels.updated":
            thread = _thread_node(g, ev, d["thread"], d["inbox_id"])
            thread.props["labels"] = [lbl["name"] for lbl in d["thread"].get("labels", [])]
            src = d["source"].upper()
            for lbl in d.get("added", []):
                lid = f"label:{lbl['name']}"
                g.add_node(Node(lid, "Label", lbl["name"], {"label_id": lbl["id"]}, ev.source_url, ev.source_hash))
                g.add_edge(thread.id, f"LABELED_BY_{src}", lid, ev)
            for lbl in d.get("removed", []):
                lid = f"label:{lbl['name']}"
                g.add_node(Node(lid, "Label", lbl["name"], {"label_id": lbl["id"]}, ev.source_url, ev.source_hash))
                g.add_edge(thread.id, f"UNLABELED_BY_{src}", lid, ev)

        elif t == "inbox.thread.assigned":
            thread = _thread_node(g, ev, d["thread"], d["inbox_id"])
            thread.props["assignee"] = d["assignee_email"]
            aid = f"person:{d['assignee_email']}"
            g.add_node(Node(aid, "Person", d["assignee_email"], {}, ev.source_url, ev.source_hash))
            g.add_edge(thread.id, f"ASSIGNED_BY_{d['source'].upper()}", aid, ev)

        elif t in ("inbox.draft.created", "inbox.draft.updated", "inbox.draft.sent", "inbox.draft.deleted"):
            thread = _thread_node(g, ev, d["thread"], d["inbox_id"])
            did = f"draft:{d['draft_id']}"
            status = {"inbox.draft.sent": "sent", "inbox.draft.deleted": "deleted"}.get(t, "pending")
            node = g.add_node(
                _node_from_event(ev, did, "Draft", f"draft {d['draft_id'][:8]}", draft_id=d["draft_id"], status=status)
            )
            if t == "inbox.draft.created":
                node.props["created_by"] = d["source"]
                g.add_edge(thread.id, f"DRAFTED_BY_{d['source'].upper()}", did, ev)
            elif t == "inbox.draft.sent":
                node.props["sent_by"] = d["source"]
                node.props["email_id"] = d.get("email_id")
                g.add_edge(did, f"SENT_BY_{d['source'].upper()}", f"message:{d['email_id']}", ev)
            elif t == "inbox.draft.deleted":
                node.props["deleted_by"] = d["source"]
                g.add_edge(did, f"DISCARDED_BY_{d['source'].upper()}", thread.id, ev)

        elif t in OUTCOME_EVENTS:
            kind = OUTCOME_EVENTS[t]
            email_id = d.get("email_id")
            message_id = d.get("message_id")
            target = by_email_id.get(email_id) or by_message_id.get(message_id)
            oid = f"outcome:{ev.id}"
            detail: dict[str, Any] = {}
            if kind == "bounced":
                detail = dict(d.get("bounce", {}))
            elif kind == "failed":
                detail = dict(d.get("failed", {}))
            elif kind == "clicked":
                detail = {"link": d.get("click", {}).get("link")}
            node = g.add_node(
                _node_from_event(
                    ev, oid, "Outcome", kind, kind=kind, at=ev.created_at, email_id=email_id,
                    message_id=message_id, correlated=target is not None, **detail,
                )
            )
            if target:
                g.add_edge(target, "RESULTED_IN", oid, ev)
                matched_by = "email_id" if by_email_id.get(email_id) else "message_id"
                node.props["matched_by"] = matched_by

    return g
