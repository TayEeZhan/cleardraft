"""The one place that calls a language model.

OWNER: Ee Zhan owns the client. Zi Qi and Sheng Kuan call it.

Centralised on purpose. Every model call in the system passes through here, so
the call count, the token spend and the cache hit rate are measurable in one
place. "82% of decisions were made by a rule" is only a defensible claim if
there is exactly one door the model can come through.

Model: claude-haiku-4-5-20251001. Cheap and fast. We are doing constrained
extraction and short classification, not reasoning, so a bigger model buys
nothing here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MODEL = "claude-haiku-4-5-20251001"


@dataclass
class ModelStats:
    """Live counters. Rendered on the accuracy screen."""

    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "failures": self.failures,
        }


STATS = ModelStats()


class ModelUnavailable(RuntimeError):
    """Raised when no API key is configured.

    Callers MUST catch this and fall back to escalation, not to a guess. The
    pipeline has to stay usable with the model switched off entirely - that is
    how we demonstrate the rule tier honestly.
    """


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete_json(prompt: str, *, schema_hint: str, max_tokens: int = 512) -> dict:
    """Ask the model for a small JSON object. Never returns free text."""
    # TODO(ee-zhan): anthropic SDK call, json parse, STATS update, retry once
    # on transient error, raise ModelUnavailable when no key.
    raise NotImplementedError
