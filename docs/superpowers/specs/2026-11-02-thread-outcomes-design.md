# Thread outcomes feedback loop for Resend Inboxes — design

Date: 2026-11-02. Author: Daniel Yarmoluk, drafted with Claude Code.

## Intent

Resend Inboxes (launched 2026-10-22) gives agents an inbox, but the outcome of
each thread stays in the dashboard and webhook stream. Build a prototype that
returns those outcomes to the agent as memory it can audit, and send it to
Resend's founder as a worked answer to "what should we build next".

## Goals

- Run in under five minutes with no account (`make demo`), and against a real
  account by pulling webhook events through the API with no public endpoint.
- Correlate `email.*` delivery outcomes to Inboxes threads, which Resend's own
  payloads do not join.
- Expose the result over MCP, the way Inboxes itself connects to agents.
- Every answer cites the stored event bytes behind it (CKG provenance rules:
  `source_url` is a fetch hint, `source_hash` is the trust anchor).

## Non-goals

- Training anything. The output is data an agent reads, not a model update.
- Sending mail or acting on threads. Read-only.
- Replacing Resend's dashboard analytics.

## Anti-goals

- A demo that only works with Daniel's account. Fixtures are first-class.
- Inferring an outcome the events do not state (no "probably delivered").

## Decisions

- Python 3.11+, `resend` SDK (2.50+ has Inboxes and `Webhooks.verify`), `mcp` 1.x.
- Storage is one JSON file per event under `data/events/`, hashed over raw bytes
  (push, fixtures) or canonical JSON (pull, since the API returns parsed JSON).
- Graph nodes: Inbox, Thread, Message, Outcome, Label, Folder, Person, Draft,
  Recipient (domain). Edge names carry the actor (`LABELED_BY_AGENT`,
  `UNLABELED_BY_DASHBOARD`) because who did it is the signal.
- Outcome correlation: `email_id` first, `message_id` (RFC Message-ID) fallback.
- Questions answered: reply latency, delivery result and bounce detail,
  complaints and spam moves, agent-label corrections, agent-draft fate,
  unanswered threads, bounce rate by recipient domain.
- Receiver is stdlib `http.server`; no web framework.

## Left to the builder / open

- Confirm on a live account that reply-to-thread's returned id equals the
  `email_id` on `email.*` events, and that failed webhook deliveries are still
  listable by API.
- `last_events()` (Emails API `last_event`) exists as a gap-filler but is not
  yet wired into the graph.
- CohereReranker-style loop awareness is irrelevant here; no async client.
