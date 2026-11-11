"""Declarative tool registry, startup profile filtering, and doctor/catalog reporting.

Follows ``docs/ARCHITECTURE.md`` §5 & ADR-0004:
- Profiles are startup-selected allowlists (`default`, `research`, `coding`,
  `documents`, `browser`, `full`).
- Only implemented, enabled tools whose optional package/key dependencies are satisfied
  are registered with the MCP server.
- Missing optional dependencies generate clear diagnostics without crashing startup.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import RetryPolicy, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel


@dataclass(frozen=True)
class ToolSpec:
    """Complete registration metadata for a single MCP tool."""

    name: str
    module: str
    description: str
    handler: Callable[..., ToolResult | Awaitable[ToolResult]]
    mcp_wrapper: Callable[..., Awaitable[Any]]
    permission: PermissionLevel
    profiles: frozenset[ProfileName]
    timeout_category: TimeoutCategory = TimeoutCategory.LOCAL
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    idempotent: bool = True
    retry_policy: RetryPolicy | None = None
    optional_packages: tuple[str, ...] = ()
    required_settings: tuple[str, ...] = ()
    produces_provenance: bool = False
    provider: str | None = None


@dataclass(frozen=True)
class ToolAvailability:
    """Diagnostic status for a registered tool under the active settings and profile."""

    name: str
    module: str
    permission: str
    profiles: list[str]
    in_selected_profile: bool
    dependencies_satisfied: bool
    enabled: bool
    disabled_reasons: list[str] = field(default_factory=list)


def is_package_available(import_name: str) -> bool:
    """Return True if ``import_name`` can be located without importing heavy modules."""
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


class ToolRegistry:
    """Central catalog of defined tools with profile and dependency filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register a ``ToolSpec``, enforcing unique tool names."""
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool registration for '{spec.name}'.")
        self._tools[spec.name] = spec

    def all_specs(self) -> list[ToolSpec]:
        """Return all registered tool specifications in deterministic order."""
        return [self._tools[k] for k in sorted(self._tools)]

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def check_availability(
        self,
        spec: ToolSpec,
        settings: Settings,
        profile: ProfileName | None = None,
    ) -> ToolAvailability:
        """Evaluate whether ``spec`` is active under ``settings`` and ``profile``."""
        active_profile = profile or settings.profile
        in_profile = active_profile == ProfileName.FULL or active_profile in spec.profiles
        reasons: list[str] = []

        if not in_profile:
            reasons.append(f"excluded by profile '{active_profile.value}'")

        missing_pkgs = [pkg for pkg in spec.optional_packages if not is_package_available(pkg)]
        if missing_pkgs:
            reasons.append(f"missing optional package(s): {', '.join(missing_pkgs)}")

        missing_keys: list[str] = []
        for attr in spec.required_settings:
            val = getattr(settings, attr, None)
            if val is None or val == "" or val == []:
                missing_keys.append(attr)
        if missing_keys:
            reasons.append(f"missing configuration/credential(s): {', '.join(missing_keys)}")

        deps_ok = not missing_pkgs and not missing_keys
        enabled = in_profile and deps_ok
        return ToolAvailability(
            name=spec.name,
            module=spec.module,
            permission=spec.permission.value,
            profiles=sorted(p.value for p in spec.profiles),
            in_selected_profile=in_profile,
            dependencies_satisfied=deps_ok,
            enabled=enabled,
            disabled_reasons=reasons,
        )

    def enabled_specs(
        self,
        settings: Settings,
        profile: ProfileName | None = None,
    ) -> list[ToolSpec]:
        """Return only tools that are both in the selected profile and have dependencies met."""
        return [
            spec
            for spec in self.all_specs()
            if self.check_availability(spec, settings, profile).enabled
        ]

    def catalog_report(
        self,
        settings: Settings,
        profile: ProfileName | None = None,
    ) -> list[dict[str, Any]]:
        """Return a serializable availability report for all known tools."""
        return [
            {
                "name": avail.name,
                "module": avail.module,
                "permission": avail.permission,
                "profiles": avail.profiles,
                "in_selected_profile": avail.in_selected_profile,
                "dependencies_satisfied": avail.dependencies_satisfied,
                "enabled": avail.enabled,
                "disabled_reasons": avail.disabled_reasons,
            }
            for spec in self.all_specs()
            for avail in (self.check_availability(spec, settings, profile),)
        ]
