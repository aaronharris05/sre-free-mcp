"""Unit tests for the LLM provider ABC + built-in backends."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sre_free_mcp.config import LLMConfig
from sre_free_mcp.llm import (
    NULL_LLM_SENTINEL,
    GeminiLLMProvider,
    LLMProvider,
    NullLLMProvider,
    default_provider,
)


def test_llm_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# NullLLMProvider
# ---------------------------------------------------------------------------


def test_null_provider_returns_sentinel():
    out = NullLLMProvider().generate("any prompt")
    assert NULL_LLM_SENTINEL in out


def test_null_provider_includes_prompt_length_for_test_assertions():
    out = NullLLMProvider().generate("hello there")
    assert "prompt_len=11" in out


def test_null_provider_ignores_temperature_and_max_tokens():
    """Both should be accepted but make no difference to output."""
    p = NullLLMProvider()
    a = p.generate("x", max_tokens=10, temperature=0.0)
    b = p.generate("x", max_tokens=1000, temperature=2.0)
    assert a == b


# ---------------------------------------------------------------------------
# GeminiLLMProvider
# ---------------------------------------------------------------------------


def test_gemini_requires_api_key():
    with pytest.raises(ValueError, match="api_key"):
        GeminiLLMProvider(api_key="")


def test_gemini_uses_configured_model():
    p = GeminiLLMProvider(api_key="k", model="gemini-2.0-pro")
    assert p.model == "gemini-2.0-pro"


# ---------------------------------------------------------------------------
# default_provider
# ---------------------------------------------------------------------------


def test_default_provider_none_returns_null_provider():
    cfg = LLMConfig(provider="none")
    assert isinstance(default_provider(cfg, project="p"), NullLLMProvider)


def test_default_provider_gemini_resolves_api_key_from_secret_manager():
    cfg = LLMConfig(provider="gemini", model="gemini-2.0-flash", api_key_secret="my-key")
    with patch("sre_free_mcp.llm.get_secret", return_value="sk-resolved"):
        provider = default_provider(cfg, project="my-proj")
    assert isinstance(provider, GeminiLLMProvider)
    assert provider.api_key == "sk-resolved"
    assert provider.model == "gemini-2.0-flash"


def test_default_provider_gemini_requires_api_key_secret():
    cfg = LLMConfig(provider="gemini", api_key_secret="")
    with pytest.raises(ValueError, match="api_key_secret"):
        default_provider(cfg, project="p")


def test_default_provider_anthropic_raises_not_implemented():
    """Reserved but not built-in — fail loud."""
    cfg = LLMConfig(provider="anthropic", api_key_secret="x")
    with pytest.raises(NotImplementedError, match="anthropic"):
        default_provider(cfg, project="p")
