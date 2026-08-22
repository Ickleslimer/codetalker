from __future__ import annotations

import logging
from typing import Sequence, Type
from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.registry")


class AdapterRegistry:
    """Central registry for harness adapters with first-class alias support."""

    def __init__(self) -> None:
        self._canonical_adapters: dict[str, BaseAdapter] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self, adapter: BaseAdapter, aliases: Sequence[str] | None = None
    ) -> None:
        """Register an initialized canonical adapter and optional aliases."""
        canonical_name = adapter.harness_name.lower()
        self._canonical_adapters[canonical_name] = adapter
        if aliases:
            for alias in aliases:
                self.register_alias(alias, canonical_name)

    def register_alias(self, alias: str, canonical_name: str) -> None:
        """Register an alias pointing to a canonical harness adapter."""
        alias_clean = alias.lower()
        target_clean = canonical_name.lower()
        if target_clean not in self._canonical_adapters:
            logger.warning(
                f"Registering alias '{alias_clean}' for un-registered target '{target_clean}'"
            )
        self._aliases[alias_clean] = target_clean

    def get(self, harness_name: str) -> BaseAdapter | None:
        """Get an adapter by canonical name or alias (case-insensitive)."""
        key = harness_name.lower()
        if key in self._canonical_adapters:
            return self._canonical_adapters[key]
        if key in self._aliases:
            target = self._aliases[key]
            return self._canonical_adapters.get(target)
        return None

    def get_canonical_name(self, harness_name: str) -> str:
        """Resolve a harness name or alias to its canonical name."""
        key = harness_name.lower()
        return self._aliases.get(key, key)

    def list_canonical_harnesses(self) -> list[str]:
        """Return a sorted list of unique canonical harness names."""
        return sorted(self._canonical_adapters.keys())

    def list_harnesses(self, include_aliases: bool = True) -> list[str]:
        """Return a sorted list of all supported harness names and aliases."""
        if not include_aliases:
            return self.list_canonical_harnesses()
        all_names = set(self._canonical_adapters.keys()) | set(self._aliases.keys())
        return sorted(all_names)

    def list_aliases(self) -> dict[str, str]:
        """Return a copy of the alias map."""
        return dict(self._aliases)


# Global registry singleton
registry = AdapterRegistry()
