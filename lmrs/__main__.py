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

import asyncio
import sys
from typing import List, Optional

DEFAULT_CONFIG_PATH = "/etc/lmrs/config.json"


def _config_path_from_args(args: List[str]) -> str:
    """Return the configuration path from a ``--config`` argument.

    Args:
        args: Argument vector to scan for ``--config <path>``.

    Returns:
        The path following ``--config``, or the default configuration path.
    """
    if "--config" in args:
        index = args.index("--config")
        if index + 1 < len(args):
            return args[index + 1]
    return DEFAULT_CONFIG_PATH


def main(argv: Optional[List[str]] = None) -> None:
    """Run the LMRS adapter server.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        None. Runs the adapter factory, which serves until shutdown.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = _config_path_from_args(args)
    from mcp_proxy_adapter.core.app_factory import create_and_run_server

    asyncio.run(create_and_run_server(config_path=config_path))


if __name__ == "__main__":
    main()
