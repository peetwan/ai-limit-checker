"""ai-limit-checker: check Claude Code and Antigravity CLI usage from the terminal.

Submodules are imported lazily so that, for example, ``--version`` does not pull
in the HTTP machinery.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

try:
    __version__ = version("ai-limit-checker")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__", "check_antigravity", "check_claude", "main"]

_LAZY = {
    "main": ("cli", "main"),
    "check_claude": ("claude", "check_claude"),
    "check_antigravity": ("antigravity", "check_antigravity"),
}

if TYPE_CHECKING:  # pragma: no cover
    from .antigravity import check_antigravity
    from .claude import check_claude
    from .cli import main


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attr = target
    return getattr(import_module(f".{module}", __name__), attr)


def __dir__() -> list[str]:
    return sorted(__all__)
