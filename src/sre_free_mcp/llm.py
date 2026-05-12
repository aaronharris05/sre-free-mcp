"""LLM provider — abstract interface + built-in backends.

The :class:`LLMProvider` ABC defines the surface: one method,
``generate(prompt, *, max_tokens, temperature) -> str``.

Built-in backends:

* :class:`NullLLMProvider` — returns a deterministic sentinel string;
  used in tests and when ``provider='none'`` in install.yaml.
* :class:`GeminiLLMProvider` — calls Google's Gemini via ``google-genai``.

To add Anthropic / OpenAI / a local model, inherit from
:class:`LLMProvider` and implement :meth:`generate`. The factory
:func:`default_provider` is the only place that hard-codes provider
names; new backends become a YAML choice once added there.
"""

from __future__ import annotations

import abc
import logging

from sre_free_mcp.config import LLMConfig
from sre_free_mcp.secrets import get_secret

logger = logging.getLogger(__name__)

NULL_LLM_SENTINEL = "[null-llm]"


class LLMProvider(abc.ABC):
    """Abstract LLM. Subclass + implement :meth:`generate`."""

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        """Return one completion as a plain string."""


# ---------------------------------------------------------------------------
# Concrete backends
# ---------------------------------------------------------------------------


class NullLLMProvider(LLMProvider):
    """Deterministic sentinel — used in tests and when LLM is disabled.

    Returns a string that includes :data:`NULL_LLM_SENTINEL` so callers
    can detect it and skip publishing model-dependent output (e.g. the
    anomaly email's narrative section).
    """

    def __init__(self, model: str = "null") -> None:
        self.model = model

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        # Echo prompt length so tests can detect generate() was called
        # without coupling to the prompt content.
        return f"{NULL_LLM_SENTINEL} model={self.model} prompt_len={len(prompt)}"


class GeminiLLMProvider(LLMProvider):
    """Google Gemini via ``google-genai``. Lazy-imports the SDK so a
    test run with the null provider doesn't need the dependency."""

    def __init__(self, *, api_key: str, model: str = "gemini-2.0-flash") -> None:
        if not api_key:
            raise ValueError("GeminiLLMProvider requires an api_key")
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        from google import genai  # lazy

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        return getattr(response, "text", "") or ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def default_provider(config: LLMConfig, *, project: str) -> LLMProvider:
    """Build the configured provider.

    ``provider='none'`` (the default) returns :class:`NullLLMProvider`.
    Other providers fetch their API key from Secret Manager.
    """
    if config.provider == "none":
        return NullLLMProvider()

    if config.provider == "gemini":
        if not config.api_key_secret:
            raise ValueError("llm.provider=gemini requires api_key_secret in install.yaml")
        api_key = get_secret(config.api_key_secret, project=project)
        return GeminiLLMProvider(api_key=api_key, model=config.model or "gemini-2.0-flash")

    # anthropic + openai are reserved but not built-in yet — fail loud
    # so the user knows to ship a custom subclass.
    raise NotImplementedError(
        f"LLM provider {config.provider!r} is not built-in. Subclass "
        "LLMProvider and wire it in your own runner entrypoint."
    )


__all__ = [
    "GeminiLLMProvider",
    "LLMProvider",
    "NULL_LLM_SENTINEL",
    "NullLLMProvider",
    "default_provider",
]
