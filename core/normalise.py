"""Stage 3a: reduce two differently-formatted values to a comparable form.

OWNER: Ee Zhan.

This module decides half the score, so it states its reasoning.

EVERY planted defect in this dataset is SUBSTANTIVE, never cosmetic:
  entities -> a different company entirely
  ports    -> a different port entirely
  counts   -> differ by at least 1
  weights  -> differ by 500, 1000 or 2000 kg
(confirmed in the organiser generator, shipment.py:_mutate)

Therefore normalisation must be AGGRESSIVE about formatting and EXACT about
content. There is no case where a fuzzy threshold helps, and every fuzzy
threshold risks swallowing a real defect. We do not use one.
"""
from __future__ import annotations

from core.types import CompareField


def normalise(field: CompareField, value: str) -> "str | int | None":
    """Return the comparable form, or None when the value is blank."""
    # TODO(ee-zhan): dispatch per field.
    raise NotImplementedError


def normalise_entity(value: str) -> str:
    """shipper / consignee / notify_party.

    Uppercase, collapse whitespace, drop trailing punctuation. Keep the full
    legal name: "MOORIM SP CO., LTD" and "MOORIM SP CO LTD" must match, but
    "MOORIM SP" and "UAB NOVAKOPA" must not.
    """
    raise NotImplementedError


def normalise_port(value: str) -> str:
    """port_of_loading / port_of_discharge.

    Strip the trailing UN/LOCODE in brackets. The .txt renderer writes
    "CALLAO, PERU (PECLL)" while the .pdf renderer writes "CALLAO, PERU" for
    the very same shipment. Comparing those raw produces a false defect.
    """
    raise NotImplementedError


def normalise_container_count(value: str) -> "int | None":
    """container_count.

    Documents write "6 x 40'HC". Only the 6 is compared - container SIZE is
    not one of the seven fields, and including it would invent defects.
    """
    raise NotImplementedError


def normalise_weight(value: str) -> "int | None":
    """gross_weight_kg.

    Strip thousands separators and the KG/KGS suffix. The .xlsx renderer
    stores a bare number, the others store "21,577 KG". Return an int.
    """
    raise NotImplementedError
