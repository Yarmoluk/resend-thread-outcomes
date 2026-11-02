"""Three ways to get Resend events into the store.

- ``pull``: list a webhook's delivered events through the Resend API and fetch
  each payload. Needs an API key and a webhook id, not a public endpoint.
- ``serve``: a small HTTP receiver that verifies the Svix signature with the
  SDK and stores the raw body.
- ``load_fixtures``: read event JSON files from a directory (the demo path).
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .store import EventStore, StoredEvent

API_BASE = "https://api.resend.com"


def _resend():
    import resend

    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise SystemExit("RESEND_API_KEY is not set")
    resend.api_key = key
    return resend


def pull(store: EventStore, webhook_id: str, *, max_events: int = 500) -> list[StoredEvent]:
    """Fetch every event delivered to ``webhook_id`` that the store lacks."""
    resend = _resend()
    fetched: list[StoredEvent] = []
    after: str | None = None
    seen = 0
    while seen < max_events:
        params: dict[str, Any] = {"limit": 100}
        if after:
            params["after"] = after
        page = resend.Webhooks.list_events(webhook_id, params)
        items = page.get("data", [])
        if not items:
            break
        for item in items:
            seen += 1
            after = item["id"]
            if store.has(item["id"]):
                continue
            event = resend.Webhooks.get_event(webhook_id, item["id"])
            fetched.append(
                store.put(
                    item["id"],
                    dict(event["payload"]),
                    source_url=f"{API_BASE}/webhooks/{webhook_id}/events/{item['id']}",
                    received_via="pull",
                )
            )
        if not page.get("has_more"):
            break
    return fetched


def last_events(email_ids: list[str]) -> dict[str, str]:
    """``last_event`` per outbound email id, from the Emails API.

    Covers a gap in the webhook stream (an endpoint that was down, a webhook
    created after the mail went out). Returns only the ids that resolve.
    """
    resend = _resend()
    result: dict[str, str] = {}
    for email_id in email_ids:
        try:
            email = resend.Emails.get(email_id)
        except Exception:  # noqa: BLE001 - a 404 just means not a sent email
            continue
        if email.get("last_event"):
            result[email_id] = email["last_event"]
    return result


def load_fixtures(store: EventStore, directory: Path) -> list[StoredEvent]:
    loaded = []
    for path in sorted(Path(directory).glob("*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw)
        event_id = payload.get("id") or path.stem
        loaded.append(
            store.put(
                event_id,
                {k: v for k, v in payload.items() if k != "id"},
                source_url=f"fixture://{path.name}",
                received_via="fixture",
                raw=raw,
            )
        )
    return loaded


def make_receiver(store: EventStore, webhook_secret: str | None):
    """Build the HTTP handler class for ``serve``."""
    import resend

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            svix_id = self.headers.get("svix-id", "")
            if webhook_secret:
                try:
                    payload = resend.Webhooks.verify(
                        {
                            "payload": raw.decode(),
                            "headers": {
                                "id": svix_id,
                                "timestamp": self.headers.get("svix-timestamp", ""),
                                "signature": self.headers.get("svix-signature", ""),
                            },
                            "webhook_secret": webhook_secret,
                        }
                    )
                except ValueError as e:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(str(e).encode())
                    return
            else:
                payload = json.loads(raw)
            if not svix_id:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"missing svix-id header")
                return
            store.put(
                svix_id,
                dict(payload),
                source_url=f"resend-webhook://{svix_id}",
                received_via="push",
                raw=raw,
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):  # quieter default logging
            print(f"[receiver] {fmt % args}")

    return Receiver


def serve(store: EventStore, port: int, webhook_secret: str | None) -> None:
    server = HTTPServer(("0.0.0.0", port), make_receiver(store, webhook_secret))
    mode = "verified" if webhook_secret else "UNVERIFIED (set RESEND_WEBHOOK_SECRET)"
    print(f"listening on :{port}, signatures {mode}")
    server.serve_forever()
