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


_REGISTRY: dict[str, DocumentParser] = {}


def register(parser: DocumentParser) -> DocumentParser:
    for ext in parser.extensions:
        _REGISTRY[ext.lower()] = parser
    return parser


def for_path(path: str) -> DocumentParser | None:
    return _REGISTRY.get(os.path.splitext(path)[1].lower())


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
