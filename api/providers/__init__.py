"""Provider registry: exposes the set of platform adapters available to the
application.  Callers use :func:`active_providers` / :func:`get_provider` rather
than importing specific implementations so the UI / aggregation layer stays
provider agnostic."""

from __future__ import annotations

from typing import Iterator

import config_manager
from api.providers.base import (
    FetchError,
    ModelUsage,
    Provider,
    ProviderSummary,
    ProviderBalance,
)
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider


PROVIDERS: dict[str, type[Provider]] = {
    DeepSeekProvider.id: DeepSeekProvider,
    MiMoProvider.id: MiMoProvider,
}


def get_provider(provider_id: str) -> Provider:
    provider_cls = PROVIDERS.get(provider_id)
    if provider_cls is None:
        raise KeyError(f"未知 provider: {provider_id}")
    return provider_cls()


def list_providers() -> list[tuple[str, str]]:
    """Return ``(id, display_name)`` for every registered provider, preserving
    registration order."""
    return [(provider.id, provider.name) for provider in (cls() for cls in PROVIDERS.values())]


def active_providers() -> Iterator[Provider]:
    """Iterate providers matching the currently selected ``ACTIVE_PROVIDER``
    configuration key (single-provider mode), or fall back to the first
    registered provider if nothing is configured.

    The configuration key is case-insensitive and typically written by the
    settings UI from a radio group.  Fallback keeps the default flow working
    before the user has explicitly chosen a provider.
    """
    selected = str(config_manager.get("ACTIVE_PROVIDER", "")).strip().lower()
    registry = PROVIDERS
    if not selected:
        # Pick the first available provider; useful for first-run where the
        # user has not yet opened the settings dialog.
        provider_ids = list(registry.keys())
        if provider_ids:
            yield PROVIDERS[provider_ids[0]]()
        return
    if selected in registry:
        yield PROVIDERS[selected]()
        return
    # 配置校验通常会拦截未知值；若内存被外部调用方直接改写，仍安全回退到 DeepSeek。
    if registry:
        yield next(iter(registry.values()))()


__all__ = [
    "FetchError",
    "ModelUsage",
    "Provider",
    "ProviderBalance",
    "ProviderSummary",
    "PROVIDERS",
    "get_provider",
    "list_providers",
    "active_providers",
]
