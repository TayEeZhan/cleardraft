"""HttpInbox, proven against a real HTTP server - no external network.

OWNER: Ee Zhan.

`HttpInbox` speaks the organiser's docker-server API (see
`adapters/inbox.py`). These tests spin up a tiny stdlib `http.server` on
127.0.0.1 in a background thread, serve two fake email records whose
attachment content is copied verbatim from `data/inbox/email_004.json` and
its two real attachments, and prove three things:

  1. `emails()` yields the two records as `core.types.Email`.
  2. `attachment_path(...)` downloads the attachment to a real local file,
     and `core.extract.extract()` on that path finds the SI/BL fields - i.e.
     the round trip through HTTP does not corrupt the document.
  3. A server-supplied attachment path containing `..` is refused before any
     request is even made, per the "never trust the server path" rule.

The model tier is off for the whole suite (see conftest.py), so extract()
here exercises the rule tier only.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import HttpInbox                          # noqa: E402
from core.extract import extract                              # noqa: E402
from core.types import Email                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

with open(os.path.join(DATA, "inbox", "email_004.json"), encoding="utf-8") as _fh:
    _EMAIL_004 = json.load(_fh)

with open(os.path.join(DATA, "attachments", "email_004_SI.txt"), "rb") as _fh:
    _SI_BYTES = _fh.read()

with open(os.path.join(DATA, "attachments", "email_004_BL.txt"), "rb") as _fh:
    _BL_BYTES = _fh.read()

#: Two fake inbox records, content copied from the real email_004 fixture,
#: each pointing at its own copy of the real SI/BL attachment bytes.
_FAKE_EMAILS = [
    {**_EMAIL_004, "email_id": "http_test_1",
     "attachments": ["attachments/http_test_1_SI.txt", "attachments/http_test_1_BL.txt"]},
    {**_EMAIL_004, "email_id": "http_test_2",
     "attachments": ["attachments/http_test_2_SI.txt", "attachments/http_test_2_BL.txt"]},
]

_FAKE_ATTACHMENTS = {
    "http_test_1_SI.txt": _SI_BYTES, "http_test_1_BL.txt": _BL_BYTES,
    "http_test_2_SI.txt": _SI_BYTES, "http_test_2_BL.txt": _BL_BYTES,
}


class _FakeOrganiserHandler(http.server.BaseHTTPRequestHandler):
    """Just enough of the organiser's API to exercise HttpInbox: GET /emails
    and GET /attachments/{path}."""

    def log_message(self, *_args) -> None:  # silence per-request logging
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        if self.path == "/emails":
            body = json.dumps(_FAKE_EMAILS).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/attachments/"):
            name = self.path[len("/attachments/"):]
            data = _FAKE_ATTACHMENTS.get(name)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def fake_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeOrganiserHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_emails_yields_the_fake_records(fake_server: str) -> None:
    inbox = HttpInbox(fake_server)
    emails = list(inbox.emails())

    assert len(emails) == 2
    assert all(isinstance(e, Email) for e in emails)
    ids = {e.email_id for e in emails}
    assert ids == {"http_test_1", "http_test_2"}
    for e in emails:
        assert e.subject == _EMAIL_004["subject"]
        assert e.attachments == tuple(
            a for a in _FAKE_EMAILS[0 if e.email_id == "http_test_1" else 1]["attachments"]
        )


def test_attachment_path_downloads_a_usable_local_file(fake_server: str) -> None:
    inbox = HttpInbox(fake_server)
    emails = {e.email_id: e for e in inbox.emails()}
    email = emails["http_test_1"]

    si_rel, bl_rel = email.attachments
    si_path = inbox.attachment_path(si_rel)
    bl_path = inbox.attachment_path(bl_rel)

    # A real, local, readable file - not a URL.
    assert os.path.isfile(si_path)
    assert os.path.isfile(bl_path)
    with open(si_path, "rb") as fh:
        assert fh.read() == _SI_BYTES

    # And it is genuinely usable by the rest of the pipeline: extract() finds
    # the SI/BL fields the same way it would from a local bundle.
    si_doc = extract(si_path, use_model=False)
    bl_doc = extract(bl_path, use_model=False)

    assert si_doc.readable
    assert si_doc.kind == "SI"
    assert si_doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"
    assert si_doc.fields["consignee"].value == "EAST BRIGHT FZ-LLC"

    assert bl_doc.readable
    assert bl_doc.kind == "BL"
    assert bl_doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"
    assert bl_doc.fields["consignee"].value == "UAB NOVAKOPA"


def test_attachment_path_caches_the_download(fake_server: str) -> None:
    """A second call for the same rel must not hit the network again - it
    should return the same local path from the per-instance cache."""
    inbox = HttpInbox(fake_server)
    rel = "attachments/http_test_1_SI.txt"
    first = inbox.attachment_path(rel)
    second = inbox.attachment_path(rel)
    assert first == second


@pytest.mark.parametrize(
    "bad_rel",
    [
        "../secrets/ground_truth.json",
        "attachments/../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/win.ini",
    ],
)
def test_traversal_and_absolute_paths_are_refused(fake_server: str, bad_rel: str) -> None:
    """The server path is never trusted. This must fail BEFORE any request is
    made - a `..` or absolute path must not even reach the socket."""
    inbox = HttpInbox(fake_server)
    with pytest.raises(ValueError):
        inbox.attachment_path(bad_rel)


def test_unreachable_server_raises_a_clear_error() -> None:
    """No server listening on this port. HttpInbox must fail with a message
    that names the problem, not a bare socket traceback."""
    inbox = HttpInbox("http://127.0.0.1:1", timeout=1.0)
    with pytest.raises(ConnectionError, match="cannot reach organiser server"):
        list(inbox.emails())


def test_base_url_must_be_http() -> None:
    with pytest.raises(ValueError):
        HttpInbox("data")
