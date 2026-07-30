"""The client-side ``pipeline`` console script: live acceptance of an LMRS server.

Implements the fleet pipeline contract on the installed client: ``pipeline``
with no arguments runs the whole suite, ``pipeline <check>`` runs one named
check, ``pipeline --list`` enumerates them. An operator anywhere can
``pip install lmrs-client``, export the ``LMRS_LIVE_*`` connection settings,
and prove a deployment with one command.

Checks, in order: the offline CLI surface self-check, then the live checks -
the exhaustive-documentation check on ``info``, the prompt-admission invariant
(an oversized prompt is refused before the runtime), and the full
all-commands drive. The live implementations live in
``lmrs_client.live_check`` and are shared verbatim with the server
repository's pipeline, so there is exactly one runner.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from lmrs_client.live_check import run_commands_live, run_info_docs_live, run_prompt_admission_live


@dataclass(frozen=True)
class Check:
    """One named verification check.

    Attributes:
        name: Stable check name used on the command line.
        description: One-line description shown by ``pipeline --list``.
        run: Zero-argument callable returning an integer exit status, 0 for pass.
    """

    name: str
    description: str
    run: Callable[[], int]


def run_cli_surface() -> int:
    """Verify the CLI covers the client surface exactly. Runs offline.

    Returns:
        0 when every client method is a subcommand and nothing extra exists.
    """
    from lmrs_client.cli import build_parser, command_methods

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions  # noqa: SLF001 - argparse offers no public accessor
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    exposed = {name.replace("-", "_") for name in subparsers.choices}
    methods = set(command_methods())
    if exposed != methods:
        print(f"FAIL cli-surface: CLI vs client mismatch: {sorted(exposed ^ methods)}")
        return 1
    print(f"PASS cli-surface: {len(methods)} commands exposed")
    return 0


CHECKS: tuple[Check, ...] = (
    Check("cli-surface", "Offline: the CLI exposes exactly the client's command surface.", run_cli_surface),
    Check("info-docs-live", "The deployed server documents itself exhaustively through info.", run_info_docs_live),
    Check("prompt-admission-live", "The deployed server refuses an oversized prompt before the runtime.", run_prompt_admission_live),
    Check("commands-live", "Drive every public LMRS command against the deployed server.", run_commands_live),
)


def _run_all() -> int:
    """Run every check in order without early exit.

    Returns:
        0 when every check passed, 1 otherwise.
    """
    failed = 0
    for check in CHECKS:
        print(f"--- {check.name}", flush=True)
        status = check.run()
        print(f"{'PASS' if status == 0 else 'FAIL'} {check.name}", flush=True)
        if status != 0:
            failed += 1
    print(f"pipeline summary: {len(CHECKS) - failed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """Entry point of the client-side ``pipeline`` command.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit status per the pipeline CLI contract.
    """
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Run the LMRS live acceptance suite or a single named check.",
    )
    parser.add_argument("check", nargs="?", help="name of a single check to run")
    parser.add_argument("--list", action="store_true", dest="list_checks", help="list the available check names")
    args = parser.parse_args(argv)

    if args.list_checks:
        for check in CHECKS:
            print(f"{check.name}: {check.description}")
        return 0

    if args.check is not None:
        for check in CHECKS:
            if check.name == args.check:
                return check.run()
        print(f"unknown check: {args.check}")
        print("available checks: " + ", ".join(check.name for check in CHECKS))
        return 2

    return _run_all()


if __name__ == "__main__":
    raise SystemExit(main())
