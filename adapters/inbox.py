"""Inbox sources. The only module that knows where emails come from.

OWNER: Ee Zhan.

ARCHITECTURE NOTE
-----------------
`InboxSource` is a port with two adapters today: a local folder and the
organiser's HTTP server. A third adapter - Microsoft Graph or IMAP - is what
turns this from a hackathon demo into something a shipping desk could actually
run, and it is a day of work, not a redesign. That claim is only credible
because the boundary exists, so it exists.
"""
from __future__ import annotations

import json
import os
from typing import Iterator, Protocol

from core.types import Email


class InboxSource(Protocol):
    def emails(self) -> Iterator[Email]: ...
    def attachment_path(self, rel: str) -> str: ...


class LocalInbox:
    """Reads the bundle laid out as inbox/*.json plus attachments/."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.inbox_dir = os.path.join(self.root, "inbox")
        if not os.path.isdir(self.inbox_dir):
            raise FileNotFoundError(f"no inbox/ under {self.root}")

    def emails(self) -> Iterator[Email]:
        for name in sorted(os.listdir(self.inbox_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.inbox_dir, name)
            with open(path, encoding="utf-8") as fh:
                yield Email.from_json(json.load(fh))

    def attachment_path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def __len__(self) -> int:
        return sum(1 for n in os.listdir(self.inbox_dir) if n.endswith(".json"))
