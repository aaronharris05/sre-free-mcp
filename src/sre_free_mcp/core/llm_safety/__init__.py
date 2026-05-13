"""LLM safety audit — drift + adversarial-prompt smoke testing.

Two complementary checks against the configured :class:`LLMProvider`:

  1. ``llm_drift``        — runs a small fixed set of canonical prompts
                            and measures cosine similarity (token-overlap
                            proxy in v1) between today's answers and a
                            stored baseline. Drift past a threshold flags.
  2. ``llm_adversarial``  — runs a small set of adversarial prompts
                            (prompt-injection, jailbreak, harmful-content)
                            and flags responses that contain refusal-
                            negative markers.

This bot is a smoke detector, not a full eval. It catches "the LLM
provider changed underneath us" or "a prompt regression broke the
upstream model" early enough to investigate before a production agent
goes off the rails.
"""

from .audit import AuditResult, sweep
from .checks import (
    AdversarialProbe,
    DriftBaseline,
    DriftSample,
    evaluate_adversarial,
    evaluate_drift,
)

__all__ = [
    "AdversarialProbe",
    "AuditResult",
    "DriftBaseline",
    "DriftSample",
    "evaluate_adversarial",
    "evaluate_drift",
    "sweep",
]
