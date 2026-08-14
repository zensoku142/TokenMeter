"""Provider registry: exposes the set of platform adapters available to the
application.  Callers use :func:`active_providers` / :func:`get_provider` rather
than importing specific implementations so the UI / aggregation layer stays
provider agnostic."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from api.providers.base import (
    FetchError,
    ModelUsage,
    Provider,
    ProviderBalance,
    ProviderQuota,
    ProviderSummary,
    QuotaMetric,
    QuotaWindow,
)
from api.providers.codex import CodexProvider
from api.providers.cursor import CursorProvider
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider
from config import runtime as config_manager


PROVIDERS: dict[str, type[Provider]] = {
    DeepSeekProvider.id: DeepSeekProvider,
    MiMoProvider.id: MiMoProvider,
    CodexProvider.id: CodexProvider,
    CursorProvider.id: CursorProvider,
}


def get_provider(provider_id: str, config: Mapping[str, Any] | None = None) -> Provider:
    provider_cls = PROVIDERS.get(provider_id)
    if provider_cls is None:
        raise KeyError(f"未知 provider: {provider_id}")
    return provider_cls(config)


def list_providers() -> list[tuple[str, str]]:
    """Return ``(id, display_name)`` for every registered provider, preserving
    registration order."""
    return [(provider.id, provider.name) for provider in PROVIDERS.values()]


def configured_provider_ids(config: Mapping[str, Any] | None = None) -> list[str]:
    """Return every configured provider ID for one captured configuration."""

    captured = dict(config) if config is not None else config_manager.all_config()
    configured: list[str] = []
    for provider_id, provider_cls in PROVIDERS.items():
        provider: Provider | None = None
        try:
            provider = provider_cls(captured)
            if provider.is_configured():
                configured.append(provider_id)
        except Exception:
            # Configuration probing must not let one adapter block the remaining
            # providers, and the log deliberately excludes config values.
            config_manager.logger().warning(
                "Provider configuration probe failed: provider=%s", provider_id
            )
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    config_manager.logger().warning(
                        "Provider configuration probe cleanup failed: provider=%s",
                        provider_id,
                    )
    return configured


def active_providers(config: Mapping[str, Any] | None = None) -> Iterator[Provider]:
    """Iterate providers matching the currently selected ``ACTIVE_PROVIDER``
    configuration key (single-provider mode), or fall back to the first
    registered provider if nothing is configured.

    The configuration key is case-insensitive and typically written by the
    settings UI from a radio group.  Fallback keeps the default flow working
    before the user has explicitly chosen a provider.
    """
    selected = str(
        config.get("ACTIVE_PROVIDER", "") if config is not None
        else config_manager.get("ACTIVE_PROVIDER", "")
    ).strip().lower()
    registry = PROVIDERS
    if not selected:
        # Pick the first available provider; useful for first-run where the
        # user has not yet opened the settings dialog.
        provider_ids = list(registry.keys())
        if provider_ids:
            yield PROVIDERS[provider_ids[0]](config)
        return
    if selected in registry:
        yield PROVIDERS[selected](config)
        return
    # 配置校验通常会拦截未知值；若内存被外部调用方直接改写，仍安全回退到 DeepSeek。
    if registry:
        yield next(iter(registry.values()))(config)


__all__ = [
    "FetchError",
    "ModelUsage",
    "Provider",
    "ProviderBalance",
    "ProviderQuota",
    "ProviderSummary",
    "QuotaMetric",
    "QuotaWindow",
    "PROVIDERS",
    "get_provider",
    "list_providers",
    "configured_provider_ids",
    "active_providers",
]
