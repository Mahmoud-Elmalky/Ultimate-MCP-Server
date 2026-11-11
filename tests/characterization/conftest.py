"""Shared fixtures for characterization tests of the legacy single-file server.

These tests pin down the *current* behavior of ``ultimate_mcp_server.py`` before any
refactor. They never perform live network calls: every provider is replaced with a fake.
Run them with the baseline environment built from ``requirements-baseline.txt``.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_MODULE = "ultimate_mcp_server"


def _import_legacy() -> ModuleType:
    """Import a fresh copy of the legacy module from the repository root."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    sys.modules.pop(LEGACY_MODULE, None)
    return importlib.import_module(LEGACY_MODULE)


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Disable ``load_dotenv()`` and run from an empty directory.

    ``load_dotenv()`` without arguments searches upward from the *calling module's*
    directory, so a developer's real ``.env`` next to the legacy script would leak into
    tests. Patching it before import keeps the tests hermetic.
    """
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.chdir(tmp_path)
    for key in ("TAVILY_API_KEY", "YOUTUBE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def legacy(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Legacy module imported with dummy API keys (no network is touched at import)."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-youtube-key")
    module = _import_legacy()
    yield module
    sys.modules.pop(LEGACY_MODULE, None)


@pytest.fixture
def import_legacy() -> Iterator[object]:
    """Expose the raw importer for tests that need to control the environment."""
    yield _import_legacy
    sys.modules.pop(LEGACY_MODULE, None)
