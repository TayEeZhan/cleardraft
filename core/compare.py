"""Stage 3b: compare the SI against the BL. No AI, ever.

OWNER: Ee Zhan.

The SI is the reference. The BL is what the carrier typed. A field is a defect
when both sides are present, non-blank, and unequal after normalisation.

If either side is missing or blank the row is UNDECIDABLE. An undecidable row
escalates the email. It is never reported as a match and never as a defect.
"""
from __future__ import annotations

from core.types import ExtractedDoc, FieldComparison


def compare(si: ExtractedDoc, bl: ExtractedDoc) -> "tuple[FieldComparison, ...]":
    """Always returns exactly seven rows, in COMPARE_FIELDS order."""
    # TODO(ee-zhan): implement.
    raise NotImplementedError
