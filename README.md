# resend-thread-outcomes

Resend Inboxes lets an agent send and receive mail. What happened to that mail
(bounced, delivered, flagged as spam, answered, re-labeled by a person) lives in
the dashboard and in webhook events, not in the thread the agent reads.

**Status: verified against a live Resend account on 2026-11-02.** The demo runs
on hand-built fixtures; `fixtures/live-2026-11-02/` holds 19 real webhook events
(addresses redacted) and a test pins what they show. Details in
[Verified live](#verified-live-2026-11-02).

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

Bounces by recipient domain: bouncy.test 2/2 (Permanent/General ×1, Permanent/Suppressed ×1)
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

## Verified live, 2026-11-02

Run on a free Resend account with `resend` 2.50.1: one forwarding inbox, one
webhook subscribed to every `email.*` and `inbox.*` event with an endpoint that
cannot be reached, two seed mails into the inbox with `reply_to` set to
`delivered@resend.dev` and `bounced@resend.dev`, one reply-to-thread each.
The 19 events Resend recorded are in `fixtures/live-2026-11-02/`.

| Claim | Result |
|---|---|
| The `id` returned by reply-to-thread is the `email_id` on `email.*` events | Confirmed. Reply id `01a19e08-143f…` is `email_id` on `email.sent`, `email.bounced` and `inbox.email.sent`; `01a19e08-1277…` on `email.sent`, `email.delivered` and `inbox.email.sent`. The message-id fallback was never needed. |
| Webhook events stay listable and fetchable by API when delivery fails | Confirmed. The endpoint never answered; every event sat at status `attempting` and `GET /webhooks/{id}/events/{event_id}` returned the full payload. |
| Outbound inbox mail fires `email.*` events | Confirmed. Each reply produced `email.sent` and then `email.delivered` or `email.bounced` (`Permanent/General`, diagnostic `550 5.1.1`). |
| `email.*` events carry no `thread_id` or `inbox_id` | Confirmed on all 9 `email.*` payloads. `inbox.*` payloads carry both plus `email_id`, which is the join. |

One thing the docs do not say: a transactional send from the same team's domain
*to* the inbox shows up in the thread as `inbox.email.sent` with the send's own
`email_id`, alongside the `inbox.email.received` copy. The graph therefore sees
your own outbound mail into an inbox as a sent message in that thread.

The four docs pages the join was built against, with the SHA-256 of their `.md`
bytes on 2026-11-02 (from [ckg-resend](https://github.com/Yarmoluk/ckg-resend)):

| Page | sha256 |
|---|---|
| https://resend.com/docs/api-reference/inboxes/reply-thread-email.md | `daf05850f60687fb25398e96ea90e8d82d1b29a836846dc8b092f297af49e1ed` |
| https://resend.com/docs/webhooks/emails/bounced.md | `4ea4583c8e5a9d02987b4f76faf5797bd8c4ec4a81e2a266ddfe058809d339ba` |
| https://resend.com/docs/webhooks/inboxes/email-sent.md | `fc7e3986fe009bcddf25058f40e86b8e6e3d3eed65c802898537a2f2609f5788` |
| https://resend.com/docs/webhooks/event-types.md | `9f70c77f168de7c57b07be934b6b09577e65253b4d929792b1d4093e835b30e2` |

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
fixtures/live-2026-11-02/  19 events captured from a live account, addresses redacted
tests/
```

MIT.
