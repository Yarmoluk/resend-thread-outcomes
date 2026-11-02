import json
from pathlib import Path

import pytest

from thread_outcomes.graph import build_graph
from thread_outcomes.ingest import load_fixtures
from thread_outcomes.report import (
    agent_draft_outcomes,
    bounce_report,
    label_corrections,
    thread_summaries,
    unanswered_threads,
)
from thread_outcomes.store import EventStore, canonical_bytes, sha256

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "events"


@pytest.fixture
def graph(tmp_path):
    store = EventStore(tmp_path / "events")
    load_fixtures(store, FIXTURES)
    return build_graph(store)


def by_subject(graph, subject):
    return next(s for s in thread_summaries(graph) if s["subject"] == subject)


def test_fixture_hash_is_over_the_raw_file_bytes(tmp_path):
    store = EventStore(tmp_path / "events")
    load_fixtures(store, FIXTURES)
    ev = store.get("evt_09")
    assert ev.source_hash == sha256((FIXTURES / "evt_09.json").read_bytes())
    assert ev.source_url == "fixture://evt_09.json"


def test_put_is_idempotent(tmp_path):
    store = EventStore(tmp_path / "events")
    payload = {"type": "email.delivered", "created_at": "2026-10-30T10:00:00.000Z", "data": {"email_id": "x"}}
    first = store.put("evt_a", payload, source_url="u", received_via="pull")
    second = store.put("evt_a", {"type": "email.bounced", "created_at": "x", "data": {}}, source_url="u", received_via="pull")
    assert second.payload == first.payload
    assert len(store) == 1
    assert first.source_hash == sha256(canonical_bytes(payload))


def test_reply_latency_and_customer_reply(graph):
    t = by_subject(graph, "Refund for order 1041")
    assert t["reply_latency_s"] == 20 * 60 + 1
    assert t["last_outcome"] == "delivered"
    assert t["customer_replied_after_us"] is True
    assert t["agent_drafts"] == ["sent"]


def test_bounce_correlates_by_email_id_and_by_message_id(graph):
    login = by_subject(graph, "Login problem")
    assert login["bounce"] == {
        "type": "Permanent",
        "subType": "General",
        "message": "550 5.1.1 The email account that you tried to reach does not exist.",
    }
    reset = by_subject(graph, "Password reset")
    assert reset["bounce"]["subType"] == "Suppressed"
    outcomes = {o.props["email_id"]: o.props for o in graph.nodes_of("Outcome")}
    assert outcomes["m5-reply"]["matched_by"] == "email_id"
    assert outcomes["send-9f8e7d6c"]["matched_by"] == "message_id"


def test_bounce_report_by_domain(graph):
    r = bounce_report(graph)
    top = r["domains"][0]
    assert top["domain"] == "bouncy.io"
    assert (top["sent"], top["bounced"], top["bounce_rate"]) == (2, 2, 1.0)
    assert top["reasons"] == {"Permanent/General": 1, "Permanent/Suppressed": 1}
    assert r["uncorrelated_outcomes"] == 1  # the receipt sent outside Inboxes


def test_label_correction_is_detected_with_provenance(graph):
    rows = label_corrections(graph)
    assert len(rows) == 1
    row = rows[0]
    assert row["agent_label"] == "Billing"
    assert row["removed_by"] == "dashboard"
    assert row["human_label_now"] == ["Sales"]
    assert row["source_url"] == "fixture://evt_12.json"
    assert row["source_hash"].startswith("sha256:")


def test_agent_draft_outcomes(graph):
    assert agent_draft_outcomes(graph) == {"sent": 1, "deleted": 1}


def test_complaint_and_spam_move(graph):
    t = by_subject(graph, "Unsubscribe me")
    assert t["complained"] is True
    assert t["folder"] == "spam"


def test_unanswered_threads(graph):
    rows = unanswered_threads(graph, older_than_hours=4)
    assert [r["subject"] for r in rows] == ["Partnership question"]
    assert unanswered_threads(graph, older_than_hours=24) == []


def test_every_node_and_edge_cites_a_source(graph):
    for n in graph.nodes.values():
        assert n.source_url and n.source_hash.startswith("sha256:"), n.id
    for e in graph.edges:
        assert e.source_url and e.source_hash.startswith("sha256:")


def test_receiver_stores_raw_body_and_rejects_bad_signature(tmp_path):
    import io

    from thread_outcomes.ingest import make_receiver

    store = EventStore(tmp_path / "events")
    Receiver = make_receiver(store, webhook_secret=None)
    raw = json.dumps({"type": "email.delivered", "created_at": "2026-10-30T10:00:00.000Z", "data": {"email_id": "x"}}).encode()

    class Fake(Receiver):
        def __init__(self, headers):
            self.headers = headers
            self.rfile = io.BytesIO(raw)
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, code):
            self.status = code

        def end_headers(self):
            pass

    h = Fake({"Content-Length": str(len(raw)), "svix-id": "msg_123"})
    h.do_POST()
    assert h.status == 200
    assert store.get("msg_123").source_hash == sha256(raw)

    h = Fake({"Content-Length": str(len(raw))})
    h.do_POST()
    assert h.status == 400
