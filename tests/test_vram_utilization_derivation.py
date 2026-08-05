"""Tests for deriving the runtime's --gpu-memory-utilization from free VRAM.

The rule under test is the one a pinned value cannot keep: vLLM refuses to start
unless the free VRAM is at least the fraction it was given of TOTAL memory. So
every derived value must satisfy that inequality against the reading it came
from, on a card shared with another service and on a card of a different size.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmrs.vram import GpuMemoryMeasurement, derive_gpu_memory_utilization, main

_ROOT = Path(__file__).resolve().parent.parent
_ENTRYPOINT = _ROOT / "docker" / "lmrs" / "docker-entrypoint.sh"
_DEFAULT_TEMPLATE = _ROOT / "packaging" / "lmrs.default.template"
_RUNNER = _ROOT / "packaging" / "bin" / "lmrs-container"

_MIB = 1024**2

# The card the incident happened on, in the two states it was measured in: the
# embedding service held 4721 MiB, then grew to 7818 MiB without releasing it.
_TOTAL = 24576 * _MIB
_FREE_WITH_EMBED_IDLE = 19855 * _MIB
_FREE_WITH_EMBED_GROWN = 16190 * _MIB
_RESERVE = 1024 * _MIB

# The 0.1.11 deploy: free VRAM at start, and the CUDA graph capture that OOMed
# after the KV pool had been sized, dying on a 150 MiB allocation with 56 MiB
# left. Capture therefore wanted more than the 1.19 GiB that a 1 GiB reserve
# left outside the fraction.
_FREE_AT_DEPLOY = 19404 * _MIB
_OBSERVED_GRAPH_CAPTURE_BYTES = 1218 * _MIB


def test_the_derived_value_is_startable_on_a_shared_card() -> None:
    """The fraction times total never exceeds the free VRAM it came from."""
    utilization = derive_gpu_memory_utilization(_FREE_WITH_EMBED_GROWN, _TOTAL, _RESERVE)

    assert utilization * _TOTAL <= _FREE_WITH_EMBED_GROWN


def test_a_growing_co_resident_service_lowers_the_value() -> None:
    """A second process taking VRAM yields a smaller fraction, not a failed start."""
    idle = derive_gpu_memory_utilization(_FREE_WITH_EMBED_IDLE, _TOTAL, _RESERVE)
    grown = derive_gpu_memory_utilization(_FREE_WITH_EMBED_GROWN, _TOTAL, _RESERVE)

    assert grown < idle
    assert grown * _TOTAL <= _FREE_WITH_EMBED_GROWN
    assert idle * _TOTAL <= _FREE_WITH_EMBED_IDLE


def test_the_reserve_is_left_outside_the_fraction() -> None:
    """Drift headroom stays unallocated so a small growth cannot break the next start."""
    utilization = derive_gpu_memory_utilization(_FREE_WITH_EMBED_GROWN, _TOTAL, _RESERVE)

    assert utilization * _TOTAL <= _FREE_WITH_EMBED_GROWN - _RESERVE + _TOTAL / 100


def test_the_fraction_is_truncated_not_rounded() -> None:
    """Rounding up would name more memory than is free, which is the bug being fixed."""
    free_bytes = 17200 * _MIB

    utilization = derive_gpu_memory_utilization(free_bytes, _TOTAL, reserve_bytes=0)

    assert utilization == 0.69
    assert utilization * _TOTAL <= free_bytes


def test_a_bigger_card_yields_a_bigger_fraction() -> None:
    """The same code serves the local card and a rented one without a config change."""
    rented_total = 81559 * _MIB

    rented = derive_gpu_memory_utilization(81000 * _MIB, rented_total, _RESERVE)
    local = derive_gpu_memory_utilization(_FREE_WITH_EMBED_GROWN, _TOTAL, _RESERVE)

    assert rented > local
    assert rented * rented_total <= 81000 * _MIB


def test_a_full_card_is_refused_rather_than_given_a_doomed_value() -> None:
    """When the reserve leaves nothing there is no honest fraction to return."""
    with pytest.raises(ValueError, match="no allocatable fraction"):
        derive_gpu_memory_utilization(512 * _MIB, _TOTAL, _RESERVE)


@pytest.mark.parametrize(
    ("free_bytes", "total_bytes", "reserve_bytes"),
    [
        (-1, _TOTAL, _RESERVE),
        (_FREE_WITH_EMBED_IDLE, -1, _RESERVE),
        (_FREE_WITH_EMBED_IDLE, _TOTAL, -1),
        (_FREE_WITH_EMBED_IDLE, 0, _RESERVE),
        (_TOTAL + _MIB, _TOTAL, _RESERVE),
    ],
)
def test_impossible_measurements_are_refused(
    free_bytes: int, total_bytes: int, reserve_bytes: int
) -> None:
    """A nonsense reading raises instead of producing a plausible number."""
    with pytest.raises(ValueError):
        derive_gpu_memory_utilization(free_bytes, total_bytes, reserve_bytes)


def test_the_entrypoint_hook_prints_the_fraction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The container entrypoint reads the value from stdout on success."""
    monkeypatch.setattr(
        "lmrs.vram.measure_gpu_memory",
        lambda: GpuMemoryMeasurement(True, _TOTAL, _FREE_WITH_EMBED_GROWN, _TOTAL - _FREE_WITH_EMBED_GROWN),
    )

    status = main([])

    assert status == 0
    assert float(capsys.readouterr().out.strip()) * _TOTAL <= _FREE_WITH_EMBED_GROWN


def test_an_unreadable_gpu_is_a_distinct_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken host must not look like a full card, so the statuses differ."""
    monkeypatch.setattr(
        "lmrs.vram.measure_gpu_memory",
        lambda: GpuMemoryMeasurement(False, error="driver unavailable"),
    )

    status = main([])

    assert status == 1
    assert "driver unavailable" in capsys.readouterr().err


def test_a_full_card_is_reported_as_its_own_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing allocatable exits 2, so the entrypoint can say which case it hit."""
    monkeypatch.setattr(
        "lmrs.vram.measure_gpu_memory",
        lambda: GpuMemoryMeasurement(True, _TOTAL, 512 * _MIB, _TOTAL - 512 * _MIB),
    )

    status = main([])

    assert status == 2
    assert "no allocatable fraction" in capsys.readouterr().err


def test_the_shipped_default_pins_no_utilization() -> None:
    """A pinned default is what broke the 2026-08-05 start; it may not come back."""
    lines = _DEFAULT_TEMPLATE.read_text(encoding="utf-8").splitlines()
    pinned = [
        line
        for line in lines
        if not line.lstrip().startswith("#") and "--gpu-memory-utilization" in line
    ]

    assert pinned == [], (
        "the shipped default pins --gpu-memory-utilization again; it cannot be "
        f"correct on two cards or beside a growing service: {pinned}"
    )


def test_the_entrypoint_derives_the_utilization() -> None:
    """The runtime is started with a value read from the card, not from a file."""
    script = _ENTRYPOINT.read_text(encoding="utf-8")

    assert "python3 -m lmrs.vram" in script
    assert '"${utilization_args[@]}"' in script


def test_the_entrypoint_refuses_to_start_without_a_derived_value() -> None:
    """An unreadable card must stop the start, not fall through to vLLM's own default."""
    script = _ENTRYPOINT.read_text(encoding="utf-8")
    derivation = script.split("utilization_args=()", 1)[1].split("vllm serve", 1)[0]

    assert "exit 69" in derivation


def test_the_default_reserve_covers_observed_cuda_graph_capture() -> None:
    """The 0.74 start sized the KV pool and then died capturing graphs on top of it."""
    utilization = derive_gpu_memory_utilization(_FREE_AT_DEPLOY, _TOTAL)

    left_outside_the_fraction = _FREE_AT_DEPLOY - utilization * _TOTAL

    assert left_outside_the_fraction > _OBSERVED_GRAPH_CAPTURE_BYTES


def test_a_one_gibibyte_reserve_would_not_have_covered_the_capture() -> None:
    """Pins why the default grew: the first value left 1.19 GiB and capture wanted more."""
    utilization = derive_gpu_memory_utilization(_FREE_AT_DEPLOY, _TOTAL, 1024 * _MIB)

    left_outside_the_fraction = _FREE_AT_DEPLOY - utilization * _TOTAL

    assert utilization == 0.74
    assert left_outside_the_fraction < _OBSERVED_GRAPH_CAPTURE_BYTES


def test_the_runner_passes_the_reserve_into_the_container() -> None:
    """A reserve set in /etc/default/lmrs is inert unless the runner forwards it."""
    script = _RUNNER.read_text(encoding="utf-8")

    assert "-e LMRS_VRAM_RESERVE_MIB=" in script


def test_the_entrypoint_forwards_a_configured_reserve() -> None:
    """A model that captures more graphs must be tunable without a rebuild."""
    script = _ENTRYPOINT.read_text(encoding="utf-8")

    assert "--reserve-mib" in script
