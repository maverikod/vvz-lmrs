"""Repository-level named checks: tests, ruff, flake8, and mypy.

Each check invokes its tool as a subprocess from the repository root so the
tool reads its own configuration file (pyproject.toml [tool.ruff], .flake8,
mypy defaults); no linter configuration is duplicated here. The root is
resolved from this file's location, not the current working directory, so a
check behaves the same however the CLI was invoked. A missing tool is reported
as a nonzero status with a readable message rather than an exception.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline.registry import REGISTRY, Check

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_tool(module: str, *args: str) -> int:
    """Run ``python -m module args...`` from the repository root.

    Args:
        module: Module name of the tool to execute.
        *args: Extra command-line arguments for the tool.

    Returns:
        int: The tool's exit status; nonzero with a readable message when the
        tool cannot be started at all.
    """
    command = [sys.executable, "-m", module, *args]
    try:
        completed = subprocess.run(command, cwd=_REPO_ROOT)
    except OSError as error:
        print(f"pipeline: cannot run {module}: {error}", file=sys.stderr)
        return 127
    return completed.returncode


def _run_tests() -> int:
    """Run pytest over tests/.

    Returns:
        int: pytest exit status.
    """
    return _run_tool("pytest", "tests/")


def _run_ruff() -> int:
    """Run ruff check with the configuration pinned in pyproject.toml.

    Returns:
        int: ruff exit status.
    """
    return _run_tool("ruff", "check", ".")


def _run_flake8() -> int:
    """Run flake8 against the .flake8 configuration.

    Returns:
        int: flake8 exit status.
    """
    return _run_tool("flake8", ".")


def _run_mypy() -> int:
    """Run mypy over the lmrs package.

    Returns:
        int: mypy exit status.
    """
    return _run_tool("mypy", "lmrs")


REGISTRY.register(Check("tests", "Run the pytest suite under tests/.", _run_tests))
REGISTRY.register(Check("ruff", "Run ruff check with the pyproject.toml [tool.ruff] configuration.", _run_ruff))
REGISTRY.register(Check("flake8", "Run flake8 against the .flake8 configuration.", _run_flake8))
REGISTRY.register(Check("mypy", "Run mypy over the lmrs package.", _run_mypy))
