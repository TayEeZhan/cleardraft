"""Stage 1: decide what the email is asking for.

OWNER: Zi Qi.

TARGET: macro-F1 >= 0.95 on your dev slice. This criterion is 30% of the
organiser's score.

TWO TRAPS THAT WILL COST YOU POINTS
-----------------------------------
1. KEYWORDS DO NOT WORK. These subjects are all GENERAL:
     "_Reminder_Paper - Submit SI & AED_21-01-2026"
     "Pending BL Release 14_01_2026"
     "APRIL PAPER - List of Outstanding BL (BDP SG) as of 2026-01-09"
   and SI_REQUEST subjects routinely contain a BL number. Match the INTENT
   VERB (compare / send / provide / query / notify), not the nouns SI and BL.

2. INTENT IS NOT THE SAME AS CATEGORY, and the difference matters downstream.
     "Please assist to send the draft BL for X for checking"
         -> category BL_COMPARISON, intent "send_doc"
     "Please compare the SI and draft BL for X and confirm"
         -> category BL_COMPARISON, intent "compare"
   Both are BL_COMPARISON. Only the second one escalates when the attachments
   are absent. 94 of the 220 comparison emails have zero attachments and 91 of
   those are ground-truth OK. Returning intent="compare" for all of them
   destroys our escalation precision. See PLAN.md section 1.6.

The `evidence` field must hold the phrase that decided it. The review UI shows
it, and it is how you debug a confusion-matrix cell at 2am.
"""
from __future__ import annotations

from core.types import Classification, Email

#: Below this, hand the email to the model instead of trusting the rule.
CONFIDENCE_FLOOR = 0.60


def classify(email: Email, *, use_model: bool = True) -> Classification:
    """Rules first, model only on the residue. Never raises."""
    # TODO(zi-qi): implement classify_by_rule, then the model fallback.
    raise NotImplementedError("Zi Qi owns core/classify.py")


def classify_by_rule(email: Email) -> Classification | None:
    """High-precision rules. Return None rather than guessing.

    Precision matters more than recall here: an unsure rule should decline so
    the model can decide, because a wrong rule is silent and a declined rule
    is measurable.
    """
    # TODO(zi-qi): implement.
    raise NotImplementedError("Zi Qi owns core/classify.py")
