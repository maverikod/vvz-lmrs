"""LMRS server entry point.

Runs the LMRS service as an ``mcp_proxy_adapter`` server. The adapter CLI
starts the Hypercorn server with the given configuration; LMRS commands are
loaded through the adapter command auto-discovery configured in the file.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import sys
from typing import List, Optional

DEFAULT_CONFIG_PATH = "/etc/lmrs/config.json"


def main(argv: Optional[List[str]] = None) -> None:
    """Run the LMRS adapter server.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        None. Delegates to the adapter CLI, which runs the server.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" not in args:
        args = ["--config", DEFAULT_CONFIG_PATH, *args]
    from mcp_proxy_adapter.cli import main as cli_main

    sys.argv = ["mcp-proxy-adapter", "server", *args]
    cli_main()


if __name__ == "__main__":
    main()
