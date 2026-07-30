"""Tests pinning the container mount contract.

The 2026-07-30 incident: the image declared VOLUME for /var/lmrs/hf-cache while
the runner bind-mounted only the parent /var/lmrs, so docker shadowed the cache
with a fresh anonymous volume on every start and vLLM silently re-downloaded
the model instead of serving. These tests pin both halves of the fix so neither
can quietly return.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "docker" / "lmrs" / "Dockerfile"
_RUNNER = _ROOT / "packaging" / "bin" / "lmrs-container"


def test_the_image_declares_no_volumes() -> None:
    """A VOLUME declaration shadows host bind mounts; none may exist."""
    lines = _DOCKERFILE.read_text(encoding="utf-8").splitlines()
    declarations = [line for line in lines if line.strip().upper().startswith("VOLUME")]

    assert declarations == [], (
        "the Dockerfile declares VOLUME again; docker will shadow these paths "
        f"with anonymous volumes at run time: {declarations}"
    )


def test_the_runner_bind_mounts_the_cache_paths_explicitly() -> None:
    """The runner must bind hf-cache and lmcache even though the parent is bound.

    The explicit binds are what override a VOLUME declaration in an older image
    (0.1.4 and 0.1.5 both carry them), so they are part of the contract, not an
    optimization.
    """
    script = _RUNNER.read_text(encoding="utf-8")

    assert '-v "${DATA_DIR}:/var/lmrs"' in script
    assert '-v "${DATA_DIR}/hf-cache:/var/lmrs/hf-cache"' in script
    assert '-v "${DATA_DIR}/lmcache:/var/lmrs/lmcache"' in script


def test_the_runner_creates_the_bound_directories() -> None:
    """A bind of a missing host path would make docker create it as root."""
    script = _RUNNER.read_text(encoding="utf-8")
    ensure = script.split("ensure_layout()")[1].split("}")[0]

    assert '"$DATA_DIR/hf-cache"' in ensure
    assert '"$DATA_DIR/lmcache"' in ensure
