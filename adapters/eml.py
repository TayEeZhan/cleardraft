"""Parse an uploaded .eml file into the shape the pipeline needs.

OWNER: whoever built the upload-your-own-email endpoint (api/index.py). This
is the ONLY module that knows how to read raw RFC 5322 email bytes; every
other stage still speaks `core.types.Email` and plain attachment paths, so
the upload path reuses the exact same classify/extract/compare/decide chain
as the dataset path (see core/pipeline.py, scripts/export_ui_data.py).

Standard library only: `email.message_from_bytes` with `email.policy.default`.
Nothing here touches disk - it returns bytes, and the caller decides where
(if anywhere) those bytes get written, same discipline as api/index.py's
`/api/check`: fixed, sanitised filenames only, never the untrusted original.
"""
from __future__ import annotations

import email
import email.policy
import html
import re
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser

#: Hard caps so one hostile or oversized .eml cannot blow up the request.
#: The endpoint also caps the whole upload at 4 MB (api/index.py), but an
#: .eml can smuggle many small parts, so attachments are capped separately
#: here too.
MAX_ATTACHMENTS = 10
MAX_ATTACHMENTS_TOTAL_BYTES = 4 * 1024 * 1024

#: Characters allowed unescaped in a sanitised filename. Everything else
#: (path separators, "..", null bytes, unicode homoglyphs, spaces) becomes
#: "_". Deliberately conservative - this name is used to build a real path
#: on disk in a tempfile.TemporaryDirectory.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class EmlError(ValueError):
    """`data` is not a parseable email (no From, no Subject, no body)."""


@dataclass(frozen=True, slots=True)
class ParsedEml:
    """Everything the pipeline needs out of one uploaded .eml file."""

    sender: str
    subject: str
    body: str
    date: "str | None"
    message_id: "str | None"
    #: (original filename, raw bytes) for every attachment kept, in order.
    attachments: "tuple[tuple[str, bytes], ...]"


def safe_filename(name: str, index: int) -> str:
    """Turn an attacker-controlled attachment name into a safe local one.

    Only the basename is kept (no directory components survive, so
    "../../evil.txt" and "C:\\x\\y.txt" both collapse to a bare filename),
    characters outside [A-Za-z0-9._-] become "_", the result is capped at 80
    characters, and it is prefixed with the attachment's index so two
    attachments that sanitise to the same name never collide inside one
    tempdir.

    The original stem is preserved (only unsafe characters are replaced, the
    string is not rebuilt from scratch), so a name like "email_004_SI.txt"
    stays "0_email_004_SI.txt" - the pipeline's "_SI." / "_BL." filename
    fallback in core/pipeline.split_si_bl still matches it.
    """
    # Basename only: split on both slash styles so a Windows-style backslash
    # path from a hostile client is not left intact just because this code
    # runs on POSIX.
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_CHARS.sub("_", base).strip("_")
    if not cleaned:
        cleaned = "attachment"
    cleaned = cleaned[:80]
    return f"{index}_{cleaned}"


# ---------------------------------------------------------------------------
# HTML -> text fallback, used only when a message has no text/plain part.
# ---------------------------------------------------------------------------
class _HtmlToText(HTMLParser):
    """Minimal HTML-to-text: strips tags, keeps line breaks for block/<br>
    elements, unescapes entities. Good enough for an email body; this is not
    a general HTML renderer."""

    _BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = html.unescape(joined)
        # Collapse runs of blank lines the block-tag markers create, and trim
        # trailing whitespace on each line, without destroying intentional
        # paragraph breaks.
        lines = [line.strip() for line in joined.splitlines()]
        out: list[str] = []
        blank = False
        for line in lines:
            if line == "":
                if not blank:
                    out.append("")
                blank = True
            else:
                out.append(line)
                blank = False
        return "\n".join(out).strip()


def _html_to_text(markup: str) -> str:
    parser = _HtmlToText()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _get_charset_str(part: Message) -> str:
    """A part's payload as text, decoded with its own declared charset."""
    payload = part.get_content()
    if isinstance(payload, str):
        return payload
    # get_content() on a text part with policy.default always returns str;
    # this branch only guards a malformed message where it does not.
    return str(payload)


def _extract_body(msg: Message) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        try:
            return _get_charset_str(plain).strip()
        except Exception:
            pass

    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        try:
            return _html_to_text(_get_charset_str(html_part))
        except Exception:
            return ""

    return ""


def _extract_sender(msg: Message) -> str:
    """The plain address from From:, e.g. "Docs <docs@x.sg>" -> "docs@x.sg"."""
    try:
        addr = msg.get("From")
    except Exception:
        addr = None
    if addr is None:
        return ""
    try:
        # email.policy.default parses From: into an Address-aware header;
        # .addresses[0].addr_spec is the bare "user@host" form.
        addresses = getattr(addr, "addresses", ())
        if addresses:
            return addresses[0].addr_spec or ""
    except Exception:
        pass
    return str(addr).strip()


def _extract_attachments(msg: Message) -> "tuple[tuple[str, bytes], ...]":
    out: list[tuple[str, bytes]] = []
    total = 0
    try:
        parts = list(msg.iter_attachments())
    except Exception:
        parts = []

    for part in parts:
        if len(out) >= MAX_ATTACHMENTS:
            break
        filename = part.get_filename()
        if not filename:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            continue
        if total + len(payload) > MAX_ATTACHMENTS_TOTAL_BYTES:
            continue
        out.append((str(filename), payload))
        total += len(payload)

    return tuple(out)


def parse_eml(data: bytes) -> ParsedEml:
    """Parse raw .eml bytes into a ParsedEml. Raises EmlError on non-email input."""
    try:
        msg = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:
        raise EmlError(f"could not parse email: {exc}") from exc

    sender = _extract_sender(msg)
    subject = str(msg.get("Subject", "") or "").strip()
    body = _extract_body(msg)

    # A real RFC 5322 message always has at least one "Key: Value" header
    # line. Plain text with no header syntax at all (no colon-delimited
    # field name on its first line) parses with zero headers and the whole
    # input dumped into the body - `email.message_from_bytes` never raises
    # for this, so it has to be caught here rather than relying on it to
    # fail loudly.
    if not list(msg.keys()):
        raise EmlError("input has no email headers; this is not an .eml file")

    if not sender and not subject and not body:
        raise EmlError("input does not look like an email (no From, Subject, or body)")

    date = msg.get("Date")
    date_str = str(date).strip() if date is not None else None
    message_id = msg.get("Message-ID")
    message_id_str = str(message_id).strip() if message_id is not None else None

    attachments = _extract_attachments(msg)

    return ParsedEml(
        sender=sender,
        subject=subject,
        body=body,
        date=date_str or None,
        message_id=message_id_str or None,
        attachments=attachments,
    )


__all__ = ["ParsedEml", "EmlError", "parse_eml", "safe_filename"]
