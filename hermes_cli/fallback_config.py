"""Helpers for reading the effective fallback provider chain from config."""

from __future__ import annotations

from typing import Any


def _normalized_base_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip("/")


def _iter_fallback_entries(raw: Any, *, defaults: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        candidates = [{"model": raw}]
    elif isinstance(raw, dict):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = raw
    else:
        return []

    defaults = defaults or {}
    entries: list[dict[str, Any]] = []
    for entry in candidates:
        if isinstance(entry, str):
            entry = {"model": entry}
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or defaults.get("provider") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not provider or not model:
            continue

        normalized = dict(entry)
        normalized["provider"] = provider
        normalized["model"] = model

        base_url = _normalized_base_url(entry.get("base_url") or defaults.get("base_url"))
        if base_url:
            normalized["base_url"] = base_url

        api_mode = str(entry.get("api_mode") or defaults.get("api_mode") or "").strip()
        if api_mode:
            normalized["api_mode"] = api_mode

        entries.append(normalized)
    return entries


def _entry_identity(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("provider") or "").strip().lower(),
        str(entry.get("model") or "").strip().lower(),
        _normalized_base_url(entry.get("base_url")).lower(),
    )


def get_fallback_chain(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the effective fallback chain merged across config keys.

    ``model.fallback_models`` is tried first on the primary provider. The
    provider-level ``fallback_providers`` chain follows, then legacy
    ``fallback_model`` entries. Duplicate provider/model/base_url routes are
    removed while preserving the first configured occurrence.
    """

    config = config or {}
    chain: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        model_defaults = {
            "provider": model_cfg.get("provider"),
            "base_url": model_cfg.get("base_url"),
            "api_mode": model_cfg.get("api_mode"),
        }
        for entry in _iter_fallback_entries(
            model_cfg.get("fallback_models"),
            defaults=model_defaults,
        ):
            identity = _entry_identity(entry)
            if identity in seen:
                continue
            seen.add(identity)
            chain.append(entry)

    for key in ("fallback_providers", "fallback_model"):
        for entry in _iter_fallback_entries(config.get(key)):
            identity = _entry_identity(entry)
            if identity in seen:
                continue
            seen.add(identity)
            chain.append(entry)

    return chain
