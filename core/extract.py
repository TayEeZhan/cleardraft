"""Stage 2: read an attachment into located field values.

OWNER: Sheng Kuan (alias table and the four format adapters).
The two-tier orchestration and the verification gate below are Ee Zhan's.

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

import dataclasses

from core.parsers import for_path
from core.placement import locate_field_value
from core.types import COMPARE_FIELDS, ExtractedDoc, FieldValue

#: How much of the document to show the model. These are one-page documents;
#: this bound exists so a pathological file cannot blow up a request.
_MAX_PROMPT_CHARS = 6000

_SCHEMA_HINT = (
    '{"shipper": str|null, "consignee": str|null, "notify_party": str|null, '
    '"port_of_loading": str|null, "port_of_discharge": str|null, '
    '"container_count": str|null, "gross_weight_kg": str|null}'
)


def _normalise_ws(text: str) -> str:
    return " ".join(text.split()).casefold()


def verify_against_source(value: str, text: str) -> bool:
    """True when `value` really appears in the document text.

    Comparison is casefolded and whitespace-collapsed, because the model will
    tidy spacing. It is NOT fuzzy: a model that returns a company which is not
    on the page must fail this check.

    This is the whole basis for claiming the model cannot invent a consignee.
    Do not relax it into a similarity score - a gate with a threshold is not
    a gate.
    """
    if not value or not text:
        return False
    return _normalise_ws(value) in _normalise_ws(text)


def extract(path: str, *, use_model: bool = True) -> ExtractedDoc:
    """Read one attachment. Never raises.

    A file we have no adapter for is not an error; it is an unknown document,
    which the decider escalates rather than guessing at.
    """
    parser = for_path(path)
    if parser is None:
        return ExtractedDoc(
            path=path,
            kind="OTHER",
            readable=False,
            error=f"no parser registered for {path.rsplit('.', 1)[-1]!r}",
        )

    try:
        doc = parser.parse(path)
    except Exception as exc:
        # The protocol says adapters must not raise. Belt and braces: if one
        # ever does, it becomes an escalation here rather than ending a
        # 520-email batch.
        return ExtractedDoc(
            path=path,
            kind="UNREADABLE",
            readable=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    if not use_model or not doc.readable:
        return doc

    # Only an SI or a BL is worth asking about. A commercial invoice or packing
    # list escalates as wrong_doc_type regardless, so reading shipping fields
    # off it would spend money to produce nothing - and a field lifted from the
    # wrong document is worse than no field.
    if doc.kind not in ("SI", "BL"):
        return doc

    missing = [f for f in COMPARE_FIELDS if f not in doc.fields]
    if not missing:
        return doc

    return _fill_missing_with_model(doc, missing)


def _fill_missing_with_model(doc: ExtractedDoc, missing: list) -> ExtractedDoc:
    """Ask the model only for the fields the rule tier could not find.

    Every returned value must survive verify_against_source or it is dropped.
    A dropped field leaves the email short of a full set, which escalates it -
    the safe outcome. We never fall back to a guess.
    """
    from adapters.model import STATS, ModelUnavailable, available, complete_json

    if not available():
        return doc

    prompt = (
        "Read this shipping document and return ONLY the fields listed.\n"
        "Copy each value EXACTLY as it appears in the document - do not "
        "reformat, expand abbreviations, or tidy punctuation.\n"
        "Use null for any field that is not present.\n\n"
        f"Fields needed: {', '.join(missing)}\n\n"
        "--- DOCUMENT ---\n"
        f"{doc.text[:_MAX_PROMPT_CHARS]}"
    )

    try:
        answer = complete_json(prompt, schema_hint=_SCHEMA_HINT)
    except ModelUnavailable:
        # No key, no SDK, or the provider failed after a retry. The rule tier
        # result stands and the email escalates on the gap.
        return doc

    fields = dict(doc.fields)
    for field in missing:
        raw_value = answer.get(field)
        if not isinstance(raw_value, str):
            continue
        value = raw_value.strip()
        if not verify_against_source(value, doc.text):
            # The model returned something that is not on the page. Discard it.
            # This is the gate doing its job, and it is why the AI cannot
            # invent a consignee. Counted, so the claim is measurable.
            STATS.gate_rejections += 1
            continue
        placement = locate_field_value(field, value, doc.text)
        if placement is None:
            # The text exists, but only under another known field (or across
            # physical lines). Presence is not proof of correct assignment.
            STATS.placement_rejections += 1
            continue
        fields[field] = FieldValue(
            value=value,
            raw=placement.raw,
            line_no=placement.line_no,
            label=placement.label,
            decided_by="model",
        )

    return dataclasses.replace(doc, fields=fields)
