"""Place a model-read value under the field that governs its source line.

Presence and placement answer different safety questions:

1. ``verify_against_source``: did the model invent this text?
2. ``locate_field_value``: did the model assign real text to the right field?

The second check is deliberately conservative. A value under the expected
known label is strongest evidence (score 2). A value on a line whose label is
unknown is allowed (score 1), preserving model recall for unfamiliar layouts.
A value found only under a different known field is rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core import aliases
from core.types import CompareField


@dataclass(frozen=True, slots=True)
class Placement:
    line_no: int
    raw: str
    label: str
    score: int


def _value_pattern(value: str) -> "re.Pattern[str] | None":
    parts = value.strip().split()
    if not parts:
        return None
    # Models may tidy whitespace but may not alter punctuation. Matching one
    # physical line also guarantees a value spanning a line break is rejected.
    return re.compile(r"\s+".join(re.escape(part) for part in parts), re.IGNORECASE)


def _known_label(prefix: str) -> "tuple[CompareField, str] | None":
    """Right-most known alias in the text before the value.

    Right-most matters for side-by-side layouts such as
    ``Shipper: A  Consignee: B``: the label closest to B governs B.
    """
    best: "tuple[int, int, CompareField, str] | None" = None
    for alias in aliases.ORDERED:
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(p) for p in alias.split())
            + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(prefix):
            field = aliases.field_for_label(alias)
            if field is None:
                continue
            candidate = (match.start(), len(match.group(0)), field, match.group(0).strip())
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        return None
    return best[2], best[3]


def _unfamiliar_label(prefix: str, raw: str) -> str:
    candidate = prefix.strip().rstrip(":").strip()
    # With no visible label, retaining the complete source line is more honest
    # than inventing a synthetic label such as "read by model: consignee".
    return candidate or raw.strip()


def locate_field_value(field: CompareField, value: str, text: str) -> "Placement | None":
    """Best non-contradicting physical-line occurrence, or ``None``.

    Highest score wins. A contradicting occurrence does not poison a stronger
    occurrence elsewhere: a correct-label line beats an unlabelled line, and
    an unlabelled line beats a line governed by a different known field.
    """
    pattern = _value_pattern(value)
    if pattern is None or not text:
        return None

    best: "Placement | None" = None
    for line_no, line in enumerate(text.splitlines(), 1):
        match = pattern.search(line)
        if match is None:
            continue
        prefix = line[: match.start()]
        known = _known_label(prefix)
        if known is None:
            candidate = Placement(
                line_no=line_no,
                raw=line.strip(),
                label=_unfamiliar_label(prefix, line),
                score=1,
            )
        else:
            governing_field, label = known
            if governing_field != field:
                continue
            candidate = Placement(
                line_no=line_no,
                raw=line.strip(),
                label=label,
                score=2,
            )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


__all__ = ["Placement", "locate_field_value"]
