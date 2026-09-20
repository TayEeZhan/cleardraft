"""Document parser adapters, one per file format.

ARCHITECTURE NOTE (ports and adapters)
--------------------------------------
`DocumentParser` is the port. Each module in this package is an adapter.
Adding a new document format means adding one adapter and one registry entry.
No core module changes. This is what makes Idea 5 (full document-set
compliance: commercial invoice, packing list, certificate of origin) a
bounded piece of work rather than a rewrite.

OWNER: Sheng Kuan.
"""
from __future__ import annotations

import os
from typing import Protocol

from core.types import ExtractedDoc


class DocumentParser(Protocol):
    """Read one attachment into plain text plus located field values."""

    #: File extensions this adapter claims, lowercase, with the dot.
    extensions: tuple[str, ...]

    def parse(self, path: str) -> ExtractedDoc:
        """Return an ExtractedDoc.

        MUST NOT raise. A file that cannot be read returns an ExtractedDoc
        with readable=False, kind="UNREADABLE" and a populated `error`.
        Raising here would crash a 520-email batch on one corrupt PDF.
        """
        ...


#: Titles a Shipping Instruction actually carries, one per renderer:
#:     .txt   "SHIPPING INSTRUCTION"
#:     .xlsx  "BL INSTRUCTION"
#:     .pdf   "BILL OF LADING INSTRUCTION"
#: and the draft Bill of Lading carries "BILL OF LADING (DRAFT)".
#:
#: TWO OF THE THREE SI TITLES CONTAIN "BILL OF LADING" OR "BL". Testing for
#: those first labels every SI as a BL, both sides of the pair come back the
#: same kind, and decide.py escalates the whole email as wrong_doc_type. An SI
#: is literally an instruction for producing the BL, so the reliable signal is
#: the word INSTRUCTION, and it must be tested FIRST.
#:
#: This lives here, shared, because four separate parsers each re-deriving it
#: is how the rule drifts apart between formats.
_SI_MARKERS = ("INSTRUCTION",)
_BL_MARKERS = ("BILL OF LADING", "B/L")
_OTHER_MARKERS = (
    "COMMERCIAL INVOICE",
    "PACKING LIST",
    "CERTIFICATE OF ORIGIN",
)


def detect_kind(text: str) -> str:
    """Classify a document from its title text. Order matters - see above."""
    if not text:
        return "OTHER"
    upper = text.upper()
    if any(m in upper for m in _SI_MARKERS):
        return "SI"
    if any(m in upper for m in _OTHER_MARKERS):
        return "OTHER"
    if any(m in upper for m in _BL_MARKERS):
        return "BL"
    return "OTHER"


_REGISTRY: dict[str, DocumentParser] = {}


def register(parser: DocumentParser) -> DocumentParser:
    for ext in parser.extensions:
        _REGISTRY[ext.lower()] = parser
    return parser


def for_path(path: str) -> DocumentParser | None:
    return _REGISTRY.get(os.path.splitext(path)[1].lower())


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# Import the adapters so they self-register. This MUST stay at the bottom:
# each adapter does `from core.parsers import detect_kind, register`, which
# resolves against this partially-initialised module, and both names are
# defined above.
#
# Without these imports the registry is empty, for_path() returns None for
# every file, and extract() reports "no parser registered" for all 250
# attachments - a silent, total extraction failure that still exits zero.
from core.parsers import docx, pdf, txt, xlsx  # noqa: E402,F401  (side effect)
