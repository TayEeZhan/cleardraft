"""Tests for adapters/model.py's per-request budget (_BUDGET).

tests/conftest.py sets CLEARDRAFT_USE_MODEL=0 for the whole suite, so these
tests explicitly flip the model tier back on (with a fake, never-dialled-out
key) for just the calls they make, restoring nothing else about the global
test setup - no network call is ever made here; every Anthropic client is a
fake that fails or succeeds locally.
"""
from __future__ import annotations

import time

import pytest

import adapters.model as model_mod


@pytest.fixture
def model_on(monkeypatch):
    monkeypatch.setenv("CLEARDRAFT_USE_MODEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    # complete_json's one-retry pause would make this test suite slow for no
    # reason - skip the sleep, not the budget accounting around it.
    monkeypatch.setattr(model_mod.time, "sleep", lambda _seconds: None)
    yield


class _FailingMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise RuntimeError("simulated provider failure")


class _FailingClient:
    """Records every construction's `timeout` kwarg and always fails."""

    captured_timeouts: "list[float]" = []

    def __init__(self, *args, **kwargs):
        _FailingClient.captured_timeouts.append(kwargs.get("timeout"))
        self.messages = _FailingMessages()


def test_budget_is_none_outside_a_request():
    """The default: every existing caller (/api/check, /api/process-email,
    scripts/*) never sets a budget, so available()'s budget check is a
    no-op for them - unaffected by this feature."""
    assert model_mod._BUDGET.get() is None


def test_budget_counts_failed_attempts_not_just_successes(model_on, monkeypatch):
    """The whole point of the fix: STATS.calls only counts a call that
    returned, so a string of failures must still exhaust the budget - an
    outage must not let the budget "never fill" while each failed attempt
    burns ~20s of wall time.
    """
    import anthropic

    fail_messages = _FailingMessages()

    class FailingClient:
        def __init__(self, *a, **k):
            self.messages = fail_messages

    monkeypatch.setattr(anthropic, "Anthropic", FailingClient)

    budget = [3, time.monotonic() + 60]
    token = model_mod._BUDGET.set(budget)
    try:
        for _ in range(5):
            with pytest.raises(model_mod.ModelUnavailable):
                model_mod.complete_json("prompt", schema_hint="{}")
    finally:
        model_mod._BUDGET.reset(token)

    # Budget of 3: call #1 uses its internal 2-attempt retry (both fail),
    # call #2's first attempt is the 3rd and last attempt the budget allows
    # (its second attempt is skipped because available() already reports
    # the budget exhausted) - every call after that raises immediately
    # without ever reaching the fake client again.
    assert budget[0] <= 0
    assert fail_messages.calls == 3


def test_budget_deadline_stops_calls_before_any_attempt(model_on, monkeypatch):
    """A deadline already in the past must stop complete_json before it
    ever constructs a client or spends an attempt - not just eventually,
    once a slow call times out."""
    import anthropic

    fail_messages = _FailingMessages()

    class FailingClient:
        def __init__(self, *a, **k):
            self.messages = fail_messages

    monkeypatch.setattr(anthropic, "Anthropic", FailingClient)

    budget = [100, time.monotonic() - 1]  # already expired
    token = model_mod._BUDGET.set(budget)
    try:
        with pytest.raises(model_mod.ModelUnavailable):
            model_mod.complete_json("prompt", schema_hint="{}")
    finally:
        model_mod._BUDGET.reset(token)

    assert fail_messages.calls == 0
    assert budget[0] == 100  # nothing was even attempted, so nothing decremented


def test_call_timeout_is_capped_to_remaining_budget_deadline(model_on, monkeypatch):
    """The client's own per-call timeout must never let one attempt run
    past the request's own deadline."""
    import anthropic

    _FailingClient.captured_timeouts = []
    monkeypatch.setattr(anthropic, "Anthropic", _FailingClient)

    budget = [5, time.monotonic() + 2.0]  # far less than the 20s default
    token = model_mod._BUDGET.set(budget)
    try:
        with pytest.raises(model_mod.ModelUnavailable):
            model_mod.complete_json("prompt", schema_hint="{}")
    finally:
        model_mod._BUDGET.reset(token)

    assert _FailingClient.captured_timeouts  # at least one attempt was made
    for t in _FailingClient.captured_timeouts:
        assert 0 < t <= 2.0


def test_budget_none_leaves_default_timeout_unbounded_by_a_deadline(model_on, monkeypatch):
    """Without a budget (every non-dataset caller today), the timeout stays
    the plain 20s default - no deadline capping applies."""
    import anthropic

    _FailingClient.captured_timeouts = []
    monkeypatch.setattr(anthropic, "Anthropic", _FailingClient)

    assert model_mod._BUDGET.get() is None
    with pytest.raises(model_mod.ModelUnavailable):
        model_mod.complete_json("prompt", schema_hint="{}")

    assert _FailingClient.captured_timeouts == [20.0, 20.0]
