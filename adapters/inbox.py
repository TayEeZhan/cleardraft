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
import tempfile
import urllib.error
import urllib.request
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
        """Resolve `rel` against this inbox's root, CONFINED to that root.

        `rel` comes straight out of an inbox/*.json's own `attachments`
        list - for the organiser's bundle that is trustworthy, but
        `api/_dataset.py` builds a LocalInbox over an untrusted upload, and
        nothing stops a hostile email record from naming an absolute path or
        a `..` escape (`os.path.join` happily returns an absolute `rel`
        unchanged, ignoring `root` entirely). Every caller of this method
        goes through `extract()`, which reads and can RETURN the file's text
        in the API response - so an unconfined join here is a read-anything
        vulnerability, not just a traversal curiosity.

        Confinement is real-path based (resolves symlinks/`..`) and uses
        `os.path.commonpath`, not a string prefix check, which a crafted
        sibling name (e.g. `root-evil/`) can defeat. A path that resolves
        outside `root` - or that cannot even be compared to it (Windows
        raises ValueError for paths on different drives) - returns a path
        that does not exist and has no extension `core.parsers.for_path`
        recognises, so `extract()` reports it unreadable/OTHER without ever
        opening anything outside `root`.
        """
        root = os.path.realpath(self.root)
        candidate = os.path.realpath(os.path.join(root, rel))
        try:
            confined = os.path.commonpath([root, candidate]) == root
        except ValueError:
            # e.g. comparing "C:\..." to "D:\..." on Windows - definitely
            # not confined.
            confined = False
        if not confined:
            return os.path.join(root, "attachments", "_outside_root_")
        return candidate

    def __len__(self) -> int:
        return sum(1 for n in os.listdir(self.inbox_dir) if n.endswith(".json"))


class HttpInbox:
    """Reads emails and attachments from the organiser's HTTP inbox server.

    Speaks the same public API the participant loader (`loader.py`) uses
    against the organiser's docker server (`docker/server/app.py`):

        GET /emails                  -> list of email records (no labels)
        GET /attachments/{path}      -> raw bytes of one attachment

    Standard library only. Each attachment is downloaded on demand into a
    private per-instance temp directory and cached there, so `extract()` -
    which needs a real path on disk - keeps working unchanged.

    The server path is never trusted as a local path: `rel` is validated
    before it touches the filesystem, and the local filename is derived from
    it rather than used directly, so a hostile or buggy server response
    cannot escape the temp directory.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        base_url = base_url.strip()
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise ValueError(f"base_url must start with http:// or https://: {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._tmpdir = tempfile.mkdtemp(prefix="cleardraft_http_inbox_")
        self._downloaded: "dict[str, str]" = {}

    # -- wire access -------------------------------------------------------
    def _get(self, path: str) -> bytes:
        url = self.base_url + path
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"organiser server at {self.base_url!r} returned "
                f"HTTP {exc.code} for {path!r}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"cannot reach organiser server at {self.base_url!r} "
                f"(is the docker server running?): {exc.reason}"
            ) from exc
        except OSError as exc:
            # Covers socket timeouts raised outside URLError on some
            # platforms/Python versions.
            raise ConnectionError(
                f"cannot reach organiser server at {self.base_url!r}: {exc}"
            ) from exc

    # -- InboxSource protocol ----------------------------------------------
    def emails(self) -> Iterator[Email]:
        records = json.loads(self._get("/emails"))
        for raw in records:
            yield Email.from_json(raw)

    def attachment_path(self, rel: str) -> str:
        """Download `rel` (as it appears in `email.attachments`) if needed,
        and return a LOCAL path to the cached copy."""
        if rel in self._downloaded:
            return self._downloaded[rel]

        local_name = self._safe_local_name(rel)
        data = self._get("/" + rel.lstrip("/"))

        local_path = os.path.join(self._tmpdir, local_name)
        with open(local_path, "wb") as fh:
            fh.write(data)

        self._downloaded[rel] = local_path
        return local_path

    # -- safety --------------------------------------------------------
    @staticmethod
    def _safe_local_name(rel: str) -> str:
        """Turn a server-supplied relative path into a safe local filename.

        Never trust the server path: reject anything absolute or containing
        a `..` traversal component before it is used to build a local path,
        and flatten what remains so the result always lands directly inside
        our own temp directory.
        """
        if not rel or not rel.strip():
            raise ValueError("empty attachment path")
        normalised = rel.replace("\\", "/")
        first_segment = normalised.split("/", 1)[0]
        if normalised.startswith("/") or (len(first_segment) >= 2 and first_segment[1] == ":"):
            # leading "/" (POSIX absolute) or "C:" (Windows drive) absolute path
            raise ValueError(f"refusing absolute attachment path from server: {rel!r}")
        parts = [p for p in normalised.split("/") if p not in ("", ".")]
        if not parts:
            raise ValueError(f"empty attachment path: {rel!r}")
        if any(p == ".." for p in parts):
            raise ValueError(f"refusing attachment path with '..': {rel!r}")
        return "_".join(parts)
