from core.feedback import apply_feedback, is_problem_feedback


def test_flagged_answer_becomes_needs_review_without_erasing_evidence():
    result = apply_feedback(
        status="OK",
        review_reason=None,
        defect_fields=[],
        has_defect=False,
        feedback={"verdict": "flagged", "note": "consignee is assigned incorrectly"},
    )

    assert result == {
        "status": "NEEDS_REVIEW",
        "review_reason": "human_feedback",
        "defect_fields": [],
        "has_defect": False,
        "changed": True,
    }


def test_problem_found_during_escalation_stays_needs_review():
    feedback = {"verdict": "done", "note": "problem: attachment belongs to another shipment"}
    assert is_problem_feedback(feedback) is True
    result = apply_feedback("NEEDS_REVIEW", "unreadable", [], False, feedback)
    assert result["status"] == "NEEDS_REVIEW"
    assert result["review_reason"] == "human_feedback"
    assert result["changed"] is True


def test_confirmation_does_not_rewrite_pipeline_result():
    result = apply_feedback(
        "MISMATCH",
        None,
        ["consignee"],
        True,
        {"verdict": "confirmed", "note": ""},
    )
    assert result["status"] == "MISMATCH"
    assert result["review_reason"] is None
    assert result["defect_fields"] == ["consignee"]
    assert result["has_defect"] is True
    assert result["changed"] is False


def test_missing_feedback_is_not_a_problem():
    assert is_problem_feedback(None) is False
    assert is_problem_feedback({"verdict": "done", "note": "checked by hand: documents agree"}) is False
