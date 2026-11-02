# resend-thread-outcomes

Resend Inboxes lets an agent send and receive mail. What happened to that mail
(bounced, delivered, flagged as spam, answered, re-labeled by a person) lives in
the dashboard and in webhook events, not in the thread the agent reads.

This prototype closes the loop. It collects Resend's webhook events, correlates
delivery outcomes back to the inbox thread they belong to, and serves the result
to an agent as a small knowledge graph where every fact cites the exact event
bytes it came from.

```
$ thread-outcomes demo

| thread   | subject                    | reply latency | last outcome | customer replied | notes                                          |
| 4d8e2a1c | Refund for order 1041      | 20m           | delivered    | yes              | agent draft sent                               |
| 7c1f0a2e | Login problem              | 10m           | bounced      | no               | bounce Permanent/General                       |
| 9b2d4f6a | Invoice for Q3 sponsorship | -             | -            | no               | label corrected: Billing                       |
| c0ffee12 | Unsubscribe me             | 5m            | complained   | no               | complaint; agent draft deleted; moved to spam  |
| deadbeef | Partnership question       | -             | -            | no               | -                                              |
| feed0006 | Password reset             | 10m           | bounced      | no               | bounce Permanent/Suppressed                    |

Bounces by recipient domain: bouncy.io 2/2 (Permanent/General ×1, Permanent/Suppressed ×1)
Agent labels a person changed: "Invoice for Q3 sponsorship": agent said Billing, dashboard replaced with Sales
Agent drafts: sent 1, deleted 1
Unanswered for 4h+: Partnership question
```

## What the agent can ask

| MCP tool | Answers |
|---|---|
| `thread_outcomes` | per thread: reply latency, delivery result, bounce type/subtype/message, complaint, whether the customer replied, agent labels a person changed, agent draft status |
| `bounces` | bounce rate and reasons by recipient domain |
| `label_corrections_tool` | labels the agent applied that a person removed or replaced, with the event that recorded it |
| `agent_drafts` | how agent drafts ended: sent by a person, deleted, pending |
| `unanswered` | inbound threads with no reply after N hours |
| `verify_source` | the source URL and SHA-256 behind any node, plus the command that re-derives it |

## Where the data comes from

Resend exposes thread state (folder, labels, assignee, read) and message timing
through the Inboxes API, and delivery outcomes through `email.*` webhook events.
The two are not joined: `email.bounced` carries `email_id` and `message_id` but
no `thread_id`. This tool joins them.

| Fact | Source event | Joined on |
|---|---|---|
| message in a thread | `inbox.thread.created`, `inbox.email.received`, `inbox.email.sent` | `thread_id` |
| delivered / bounced / complained / delayed / failed / opened / clicked | `email.*` | `email_id`, else `message_id` |
| label added or removed, and by whom | `inbox.thread.labels.updated` (`source`: agent, dashboard, api, system) | `thread_id` |
| moved to spam / archive, and by whom | `inbox.thread.folder.updated` | `thread_id` |
| agent draft created, sent by a person, or deleted | `inbox.draft.*` | `draft_id` |
| assignee | `inbox.thread.assigned` | `thread_id` |
| reply latency | first inbound `received_at` to first outbound `received_at` | derived |

## Three ways to feed it

**Demo, no account needed.** Replays the fixture events in `fixtures/events/`.

```
make setup && make demo
```

**Pull, no public endpoint needed.** Resend keeps every event it tried to deliver
to a webhook and serves the payload by API. Create a webhook in the dashboard
(the URL can be anything; delivery can fail), subscribe it to the inbox and email
events, then:

```
export RESEND_API_KEY=re_...
thread-outcomes pull --webhook-id <id>
thread-outcomes report
```

**Push.** Run the receiver and point the webhook at it. Signatures are verified
with the Resend SDK's `Webhooks.verify` when `RESEND_WEBHOOK_SECRET` is set.

```
export RESEND_WEBHOOK_SECRET=whsec_...
thread-outcomes serve --port 8787
```

Then expose the graph to any MCP client:

```
thread-outcomes mcp
```

## Provenance

Each stored event is one JSON file under `data/events/` with the SHA-256 of its
bytes: the raw request body for pushed events, the file for fixtures, and a
canonical serialization of the API payload for pulled events (`thread-outcomes
canon` re-derives it from a `GET /webhooks/{id}/events/{event_id}` response).
Every node and edge in the graph names the event it came from. `verify_source`
hands that back with the command to check it:

```
$ thread-outcomes verify outcome:evt_23
{
  "node_id": "outcome:evt_23",
  "source_url": "fixture://evt_23.json",
  "source_hash": "sha256:…",
  "verify": "sha256sum fixtures/events/evt_23.json"
}
```

The hash is the trust anchor. The URL is a fetch hint.

The graph is also written as CSV and Markdown under `data/graph/` in the
Compact Knowledge Graph layout used by the [ckg-benchmark](https://github.com/Yarmoluk/ckg-benchmark)
corpus, so it can be loaded by the same tooling.

## Assumptions to confirm against a live account

- The `id` returned by reply-to-thread is the same id that appears as `email_id`
  on `email.*` events. The docs say it works with the Emails endpoints; the
  message-id fallback covers the case where it does not.
- Webhook events are retained and listable by API even when delivery to the
  endpoint failed. The list endpoint reports `failed` and `attempting` statuses,
  which suggests so.
- Outbound inbox mail fires `email.*` events like transactional mail does.

## Layout

```
thread_outcomes/
  store.py        event files with content hashes
  ingest.py       pull (API), serve (receiver), load_fixtures
  graph.py        events -> nodes and edges, outcome correlation
  report.py       the questions, as data and as Markdown
  ckg.py          CSV + Markdown export
  mcp_server.py   MCP tools over the graph
fixtures/events/  24 events across 6 threads, built from the payload shapes in Resend's docs
tests/
```

MIT.
