"""Live acceptance checks of the repository pipeline (C-062).

Registers the live checks - the all-commands drive, the prompt-admission
invariant, and the exhaustive-documentation check - against the deployed
server. The implementations live in the client package
(``lmrs_client.live_check``) and are shared verbatim with the client's own
``pipeline`` console script, so there is exactly one runner and the two
entrypoints cannot drift. This module only puts the client on the path and
registers the checks.

Skipping is a failure. When connection settings are missing or the server
cannot be reached the checks return nonzero, because a check that reports
success without running is worse than no check at all.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path

from pipeline.registry import REGISTRY, Check

_CLIENT_ROOT = Path(__file__).resolve().parent.parent / "client"
if str(_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLIENT_ROOT))

from lmrs_client.live_check import (  # noqa: E402  (path set up above)
    EXPECTED_REASONS as _EXPECTED_REASONS,
    ORDER as _ORDER,
    acceptance_models as _acceptance_models,
    arguments_for,
    run_commands_live,
    run_info_docs_live,
    run_prompt_admission_live,
)
from lmrs_client.verdict import verdict as _verdict  # noqa: E402

__all__ = [
    "_EXPECTED_REASONS",
    "_ORDER",
    "_acceptance_models",
    "_arguments",
    "_verdict",
    "run_commands_live",
]

# Kept under the historical name so existing consumers and tests keep reading
# the same seam; the implementation is the shared client one.
_arguments = arguments_for


REGISTRY.register(
    Check(
        "commands-live",
        "Drive every public LMRS command through the client against the deployed server.",
        run_commands_live,
    )
)
REGISTRY.register(
    Check(
        "prompt-admission-live",
        "Prove the deployed server refuses an oversized prompt before the runtime.",
        run_prompt_admission_live,
    )
)
REGISTRY.register(
    Check(
        "info-docs-live",
        "Verify the deployed server documents itself exhaustively through info.",
        run_info_docs_live,
    )
)
