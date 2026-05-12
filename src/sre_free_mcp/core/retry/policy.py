"""Per-workflow retry policy registry.

Each workflow can have its own backoff curve, attempt cap, and circuit-
breaker thresholds. Pure-Python; no I/O. The orchestrator consults
:func:`lookup` to decide whether (and when) to schedule the next retry.

Default policy (3 attempts, 30s/5m/30m backoff) is sized for ingest-
style transients: a 30-second blip absorbs DNS / quota hiccups; 5
minutes covers a brief upstream outage; 30 minutes is the last best-
effort before declaring the run exhausted and routing to RCA. Cost-
sensitive jobs (LLM API quota, paid third-party APIs) typically want
tighter caps — register those via :func:`register_override` from the
config loader.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Knobs the retry orchestrator reads.

    Attributes:
        max_attempts: total attempts including the original execution.
            ``max_attempts=3`` means initial run + up to 2 retries.
        backoff_seconds: tuple of seconds to wait before retry N
            (1-indexed into the tuple). ``len(backoff_seconds)`` should
            be at least ``max_attempts - 1``; the orchestrator clamps
            to the last element if attempts overshoot.
        breaker_window_min: rolling window over which the circuit
            breaker counts retry events. Default 60 minutes.
        breaker_threshold: open the breaker once this many retry events
            land in ``breaker_window_min``. Default 5.
        require_idempotent: when True, refuse to retry if the workflow's
            ``idempotent`` flag is False / NULL. Non-idempotent jobs
            (billing close, EDI submission) require HITL even on
            transient failure.
    """

    max_attempts: int = 3
    backoff_seconds: tuple[int, ...] = (30, 300, 1800)
    breaker_window_min: int = 60
    breaker_threshold: int = 5
    require_idempotent: bool = True


# Populated by the config loader at startup. Stays empty until then.
_OVERRIDES: dict[str, RetryPolicy] = {}


def register_override(workflow_name: str, policy: RetryPolicy) -> None:
    """Register a per-workflow override.

    Called by the config loader on startup (one call per row in
    ``retry_policies.yaml``). Replaces any prior override for the same
    workflow.
    """
    _OVERRIDES[workflow_name] = policy


def clear_overrides() -> None:
    """Drop all registered overrides. Mainly for tests."""
    _OVERRIDES.clear()


def lookup(workflow_name: str) -> RetryPolicy:
    """Return the retry policy for ``workflow_name``.

    Falls back to :class:`RetryPolicy` defaults when no override is
    registered.
    """
    return _OVERRIDES.get(workflow_name, RetryPolicy())


def next_backoff_seconds(policy: RetryPolicy, prior_attempts: int) -> int | None:
    """Seconds to wait before the next retry, given prior attempts.

    Returns None when retries are exhausted
    (``prior_attempts >= max_attempts``). The first retry uses
    ``backoff_seconds[0]``; if the curve is shorter than
    ``max_attempts - 1`` the last value is re-used (clamped tail).
    """
    if prior_attempts >= policy.max_attempts:
        return None
    if not policy.backoff_seconds:
        return 0
    # prior_attempts is 1-indexed: first retry = prior_attempts=1.
    idx = min(prior_attempts - 1, len(policy.backoff_seconds) - 1)
    return policy.backoff_seconds[max(0, idx)]


__all__ = [
    "RetryPolicy",
    "clear_overrides",
    "lookup",
    "next_backoff_seconds",
    "register_override",
]
