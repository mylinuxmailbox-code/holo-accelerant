"""Centralized logging and instrumentation."""
from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

_LOGGER_NAME = "accelero"


def get_logger(verbose: bool = False, quiet: bool = False) -> logging.Logger:
    """Return a configured logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    if verbose:
        logger.setLevel(logging.INFO)
    if os.environ.get("ACCELERO_DEBUG"):
        logger.setLevel(logging.DEBUG)
    if quiet:
        logger.setLevel(logging.ERROR)
    return logger


@dataclass
class Timings:
    """Phase timings for performance instrumentation."""

    phases: dict[str, float] = field(default_factory=dict)
    _starts: dict[str, float] = field(default_factory=dict)

    def start(self, name: str) -> None:
        self._starts[name] = time.perf_counter()

    def stop(self, name: str) -> None:
        if name in self._starts:
            elapsed = time.perf_counter() - self._starts.pop(name)
            self.phases[name] = self.phases.get(name, 0.0) + elapsed

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        self.start(name)
        try:
            yield
        finally:
            self.stop(name)

    def total(self) -> float:
        return sum(self.phases.values())

    def as_dict(self) -> dict[str, float]:
        return dict(self.phases)

    def report(self) -> str:
        lines = []
        for name, value in self.phases.items():
            lines.append(f"  {name:<16} {value:>8.3f}s")
        lines.append(f"  {'total':<16} {self.total():>8.3f}s")
        return "\n".join(lines)
