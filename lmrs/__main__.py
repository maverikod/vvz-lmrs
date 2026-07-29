"""LMRS server entry point.

Runs the LMRS service as an ``mcp_proxy_adapter`` server using the adapter's
``create_and_run_server`` factory. The factory validates the configuration,
builds the Hypercorn server with the configured TLS settings, registers the
service with the proxy, and serves until shutdown. LMRS commands are loaded
through the adapter command auto-discovery configured in the file.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import argparse
from typing import List, Optional

DEFAULT_CONFIG_PATH = "/etc/lmrs/config.json"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the entrypoint argument vector.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Namespace carrying the resolved ``config`` path.
    """
    parser = argparse.ArgumentParser(
        prog="lmrs",
        description="Run the LMRS adapter server.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the LMRS configuration file.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """Run the LMRS adapter server.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        None. Delegates to the adapter factory, which serves until shutdown.
    """
    args = parse_args(argv)

    # Deferred so the entrypoint stays importable without the optional
    # ``[server]`` extra. The startup sequence itself (command registration
    # plus the adapter factory call) lives in one canonical place, which
    # run_lmrs_adapter delegates to as well.
    from lmrs.adapter.runtime import start_adapter_server

    start_adapter_server(args.config)


if __name__ == "__main__":
    main()
