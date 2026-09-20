"""Draft the clerk's reply. Template fill only - never free model text.

OWNER: Ee Zhan.

The system NEVER sends. It drafts, a person reads, a person presses Send.
Every value in the draft is one we already verified against the source
document, so the reply cannot contain a number the documents do not contain.
"""
from __future__ import annotations

from core.types import Decision, Email

MISMATCH_TEMPLATE = """Hi {name},

We found {n} discrepanc{y} in the draft BL for {ref}:

{lines}

Please amend and resend the draft.
"""

CLEAR_TEMPLATE = """Hi {name},

No mismatch detected. Draft BL for {ref} is OK to proceed.
"""

REVIEW_TEMPLATE = """Hi {name},

We could not complete the check on {ref}: {reason}.
A colleague is reviewing this manually and will revert shortly.
"""


def draft_reply(email: Email, decision: Decision) -> str:
    # TODO(ee-zhan): pick the template, extract the sender's first name and
    # the OC reference from the subject, and render one line per defect as
    #     - Consignee - SI: EAST BRIGHT FZ-LLC / BL: UAB NOVAKOPA
    raise NotImplementedError
