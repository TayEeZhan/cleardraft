"""Stage 2: read an attachment into located field values.

OWNER: Sheng Kuan.

Two-tier resolution, in this order:
  1. the format adapter plus the alias table   -> decided_by="rule"
  2. the model, ONLY for fields tier 1 missed  -> decided_by="model"

THE VERIFICATION GATE IS NOT OPTIONAL.
Any value the model returns must appear verbatim in `doc.text`. If it does
not, discard it and leave the field missing, which escalates the email to
NEEDS_REVIEW. This is the mechanism that makes "the AI cannot invent a
consignee" a true statement rather than a hopeful one.
"""
from __future__ import annotations

from core.parsers import for_path
from core.types import ExtractedDoc


def extract(path: str, *, use_model: bool = True) -> ExtractedDoc:
    """Read one attachment. Never raises."""
    # TODO(sheng-kuan): dispatch to for_path(path); if no adapter, return an
    # ExtractedDoc with kind="OTHER". Then run the model fallback for any of
    # COMPARE_FIELDS still missing, behind verify_against_source().
    raise NotImplementedError("Sheng Kuan owns core/extract.py")


def verify_against_source(value: str, text: str) -> bool:
    """True when `value` really appears in the document text.

    Comparison is casefolded and whitespace-collapsed, because the model will
    tidy spacing. It is NOT fuzzy: a model that returns a company which is not
    on the page must fail this check.
    """
    # TODO(sheng-kuan): implement.
    raise NotImplementedError("Sheng Kuan owns core/extract.py")
