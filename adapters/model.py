"""The one place that calls a language model.

OWNER: Ee Zhan owns the client. Zi Qi and Sheng Kuan call it.

Centralised on purpose. Every model call in the system passes through here, so
the call count, the token spend and the cache hit rate are measurable in one
place. "82% of decisions were made by a rule" is only a defensible claim if
there is exactly one door the model can come through.

Model: claude-haiku-4-5 (exact ID, no date suffix, per the Claude API skill). Cheap and fast. We are doing constrained
extraction and short classification, not reasoning, so a bigger model buys
nothing here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODEL = "claude-haiku-4-5"


@dataclass
class ModelStats:
    """Live counters. Rendered on the accuracy screen."""

    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0
    #: Model answers thrown away because they did not appear in the source
    #: document. The anti-hallucination gate, counted rather than asserted.
    gate_rejections: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "failures": self.failures,
            "gate_rejections": self.gate_rejections,
        }


STATS = ModelStats()


class ModelUnavailable(RuntimeError):
    """Raised when no API key is configured.

    Callers MUST catch this and fall back to escalation, not to a guess. The
    pipeline has to stay usable with the model switched off entirely - that is
    how we demonstrate the rule tier honestly.
    """


def _load_dotenv() -> None:
    """Read .env into the environment if present. No extra dependency.

    Existing environment variables win, so an explicitly exported key is never
    silently overridden by a stale file.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # A malformed or unreadable .env must not take the pipeline down. The
        # caller sees ModelUnavailable and falls back to the rule tier.
        return


# Load .env ONCE, at import. Re-reading it inside available() made the model
# impossible to switch off: deleting ANTHROPIC_API_KEY from the environment was
# silently undone on the next call. Tests and the rule-tier-only demo both rely
# on "off" meaning off.
_load_dotenv()


def available() -> bool:
    """True when a key is configured and the model has not been switched off.

    CLEARDRAFT_USE_MODEL=0 turns the model tier off without touching the key -
    that is how we demonstrate, honestly, what the rules decide on their own.
    """
    if os.environ.get("CLEARDRAFT_USE_MODEL", "1").strip() == "0":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the reply, tolerating a markdown fence."""
    body = text.strip()
    if body.startswith("```"):
        body = body[3:]
        if body.lstrip().lower().startswith("json"):
            body = body.lstrip()[4:]
        if "```" in body:
            body = body[: body.rfind("```")]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model reply contained no JSON object")
    parsed = json.loads(body[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model reply was not a JSON object")
    return parsed


def complete_json(prompt: str, *, schema_hint: str, max_tokens: int = 512) -> dict:
    """Ask the model for a small JSON object. Never returns free text.

    Raises ModelUnavailable when there is no key, no SDK, or the call fails
    after one retry. Callers MUST treat that as "escalate", never as a guess.
    One retry only: this runs over 520 emails, and a long backoff chain would
    turn a provider blip into a stalled batch.
    """
    if not available():
        raise ModelUnavailable("ANTHROPIC_API_KEY is not configured")
    try:
        import anthropic
    except ImportError as exc:
        raise ModelUnavailable(f"anthropic SDK not installed: {exc}") from exc

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = (
        "You extract structured data from shipping operations email. "
        "Reply with one JSON object and nothing else. No prose, no markdown. "
        f"Required shape: {schema_hint}"
    )

    last: "Exception | None" = None
    for attempt in (1, 2):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            STATS.calls += 1
            usage = getattr(resp, "usage", None)
            if usage is not None:
                STATS.input_tokens += getattr(usage, "input_tokens", 0) or 0
                STATS.output_tokens += getattr(usage, "output_tokens", 0) or 0
            text = "".join(
                getattr(block, "text", "") for block in getattr(resp, "content", [])
            )
            return _extract_json(text)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.BadRequestError, anthropic.NotFoundError) as exc:
            # Not transient: a bad key or a bad request fails identically on
            # retry. The SDK already retries 429/5xx itself (max_retries=2), so
            # our own second attempt exists only for unparseable replies.
            last = exc
            break
        except Exception as exc:  # timeout after SDK retries, or unparseable reply
            last = exc
            if attempt == 2:
                break

    STATS.failures += 1
    raise ModelUnavailable(f"model call failed after retry: {last}") from last
