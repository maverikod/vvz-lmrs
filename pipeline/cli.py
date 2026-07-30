"""The ``pipeline`` console-script entrypoint.

Implements the three-way invocation contract: no arguments runs every
registered check in registration order without stopping at the first failure;
one positional check name runs only that check; ``--list`` enumerates the
available check names. The CLI owns argument parsing, ordering, and reporting
only: the registry is the single source of what exists, so adding a check
never requires an edit to this file.
"""

from __future__ import annotations

import argparse

import pipeline.checks  # noqa: F401  (registers the repository checks)
import pipeline.checks_live  # noqa: F401  (registers the live all-commands check)
from pipeline.registry import REGISTRY, CheckRegistry


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the pipeline CLI.

    Returns:
        argparse.ArgumentParser: Parser accepting an optional check name and --list.
    """
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Run the project verification suite or a single named check.",
    )
    parser.add_argument("check", nargs="?", help="name of a single check to run")
    parser.add_argument("--list", action="store_true", dest="list_checks", help="list the available check names")
    return parser


def _run_all(registry: CheckRegistry) -> int:
    """Run every registered check in order without early exit.

    Args:
        registry: Registry whose checks are executed.

    Returns:
        int: 0 when every check passed, 1 otherwise.
    """
    results: list[tuple[str, int]] = []
    for check in registry.all():
        # Flushed so this line lands next to the check's own output: a check
        # writes to the file descriptor directly, while print() to a pipe is
        # block-buffered and would otherwise report every verdict at exit.
        print(f"--- {check.name}", flush=True)
        status = check.run()
        results.append((check.name, status))
        print(f"{'PASS' if status == 0 else 'FAIL'} {check.name}", flush=True)
    passed = sum(1 for _, status in results if status == 0)
    failed = len(results) - passed
    print(f"pipeline summary: {passed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None, registry: CheckRegistry | None = None) -> int:
    """Entry point of the ``pipeline`` command.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
        registry: Registry to run against; defaults to the module-level REGISTRY.

    Returns:
        int: Process exit status per the pipeline CLI contract.
    """
    args = _build_parser().parse_args(argv)
    active = registry if registry is not None else REGISTRY

    if args.list_checks:
        for check in active.all():
            print(f"{check.name}: {check.description}")
        return 0

    if args.check is not None:
        try:
            check = active.get(args.check)
        except KeyError:
            print(f"unknown check: {args.check}")
            print("available checks: " + ", ".join(active.names()))
            return 2
        return check.run()

    return _run_all(active)


if __name__ == "__main__":
    raise SystemExit(main())
