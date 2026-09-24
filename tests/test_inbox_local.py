"""LocalInbox.attachment_path must confine to its own root.

OWNER: Ee Zhan.

`LocalInbox` used to just `os.path.join(self.root, rel)`. That is safe for
the organiser's own bundle, where `rel` always comes from a trusted
inbox/*.json - but api/_dataset.py builds a LocalInbox over an UNTRUSTED
upload, and every LocalInbox caller reads whatever `attachment_path()`
returns straight into a document's `text`, which can come straight back out
in an API response. An absolute path, or a `..` escape, must never resolve
outside the inbox's own root.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import LocalInbox                         # noqa: E402
from core.extract import extract                               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def _confined(root: str, path: str) -> bool:
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    try:
        return os.path.commonpath([real_root, real_path]) == real_root
    except ValueError:
        return False


def test_existing_behaviour_on_data_is_unchanged():
    """A normal, trusted relative attachment path still resolves exactly
    where it always did - the confinement check must never break the
    organiser's own bundle."""
    inbox = LocalInbox(DATA)
    path = inbox.attachment_path("attachments/email_004_SI.txt")
    assert os.path.isfile(path)
    doc = extract(path)
    assert doc.readable is True
    assert doc.kind == "SI"


def test_absolute_path_is_confined():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "inbox"))
        secret = os.path.join(tmp, "..", "outside_secret.txt")
        secret = os.path.abspath(secret)
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("TOP SECRET\n")
        try:
            inbox = LocalInbox(tmp)
            resolved = inbox.attachment_path(secret)
            assert _confined(tmp, resolved)
            assert not os.path.exists(resolved) or os.path.realpath(resolved) != os.path.realpath(secret)
            doc = extract(resolved)
            assert doc.readable is False
        finally:
            os.remove(secret)


def test_dotdot_traversal_is_confined():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "inbox"))
        os.makedirs(os.path.join(tmp, "attachments", "x_SI.d"))
        parent = os.path.dirname(tmp)
        secret = os.path.join(parent, "cd_test_secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("TOP SECRET\n")
        try:
            inbox = LocalInbox(tmp)
            rel = "attachments/x_SI.d/../../../" + os.path.basename(secret)
            resolved = inbox.attachment_path(rel)
            assert _confined(tmp, resolved)
            doc = extract(resolved)
            assert doc.readable is False
            assert "TOP SECRET" not in doc.text
        finally:
            os.remove(secret)


def test_confined_path_reports_unreadable_not_missing_parser_crash():
    """The path attachment_path() falls back to for an out-of-bounds `rel`
    must itself behave safely under extract() - no extension core.parsers
    recognises, so it comes back unreadable/OTHER rather than raising."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "inbox"))
        inbox = LocalInbox(tmp)
        resolved = inbox.attachment_path("/etc/passwd")
        doc = extract(resolved)
        assert doc.readable is False
        assert doc.text == ""
