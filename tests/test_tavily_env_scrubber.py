"""Regression coverage for Tavily credential-shaped env scrubber names."""

from __future__ import annotations

from tests.conftest import _looks_like_credential


def test_tavily_pool_env_names_are_treated_as_credentials():
    assert _looks_like_credential("TAVILY_API_KEYS") is True
    assert _looks_like_credential("TAVILY_API_KEY_1") is True
    assert _looks_like_credential("TAVILY_API_KEY_10") is True
    assert _looks_like_credential("TAVILY_API_KEY_51") is True
    assert _looks_like_credential("TAVILY_API_KEY_999999") is True
