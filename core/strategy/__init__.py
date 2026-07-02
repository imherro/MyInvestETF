"""Strategy interpretation layers for ETF research."""

from .contrarian_mode import (
    ContrarianModeEngine,
    ContrarianSignal,
    contrarian_signal_to_dict,
)

__all__ = [
    "ContrarianModeEngine",
    "ContrarianSignal",
    "contrarian_signal_to_dict",
]
