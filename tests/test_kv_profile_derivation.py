"""Tests deriving KV-cache facts from a model's own configuration.

The worked example in the specification is used as the fixed point: a Qwen3-like
profile with 48 layers, 4 KV heads, head dim 128 and two-byte elements must
produce 98304 bytes per token and 4026531840 bytes for a 40960-token window.
Weight quantization must not shrink those figures.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from lmrs.configuration import derive_kv_cache_profile, kv_element_bytes_for_dtype

QWEN3_LIKE = {
    "num_hidden_layers": 48,
    "num_attention_heads": 32,
    "num_key_value_heads": 4,
    "head_dim": 128,
    "torch_dtype": "bfloat16",
    "max_position_embeddings": 40960,
}


def test_the_specification_example_is_reproduced() -> None:
    """The documented KV figures come out of the model config unchanged."""
    profile = derive_kv_cache_profile(QWEN3_LIKE)

    assert profile is not None
    assert profile.kv_bytes_per_token() == 98304
    assert profile.kv_full_window_bytes() == 4026531840
    assert profile.declared_context_window == 40960


def test_head_dim_is_derived_when_the_config_omits_it() -> None:
    """A config without head_dim falls back to hidden size over heads."""
    config = {key: value for key, value in QWEN3_LIKE.items() if key != "head_dim"}
    config["hidden_size"] = 4096

    profile = derive_kv_cache_profile(config)

    assert profile is not None
    assert profile.head_dim == 128


def test_weight_quantization_does_not_shrink_the_kv_cache() -> None:
    """A quantized checkpoint keeps the runtime KV element size."""
    config = {**QWEN3_LIKE, "quantization_config": {"quant_method": "awq"}}

    profile = derive_kv_cache_profile(config)

    assert profile is not None
    assert profile.kv_element_bytes == 2
    assert profile.kv_bytes_per_token() == 98304
    assert profile.quantization_profile == "awq"


def test_an_explicit_kv_cache_dtype_halves_the_cost() -> None:
    """A runtime told to keep one-byte KV elements halves the KV cost."""
    profile = derive_kv_cache_profile(QWEN3_LIKE, "fp8")

    assert profile is not None
    assert profile.kv_element_bytes == 1
    assert profile.kv_bytes_per_token() == 49152


def test_auto_defers_to_the_model_dtype() -> None:
    """The `auto` setting means the model dtype decides."""
    profile = derive_kv_cache_profile(QWEN3_LIKE, "auto")

    assert profile is not None
    assert profile.kv_element_bytes == 2


def test_a_nested_text_config_is_read() -> None:
    """A multimodal config carrying its text parameters nested still resolves."""
    profile = derive_kv_cache_profile({"text_config": QWEN3_LIKE, "torch_dtype": "bfloat16"})

    assert profile is not None
    assert profile.kv_bytes_per_token() == 98304


def test_an_incomplete_config_yields_no_profile() -> None:
    """Missing parameters produce no profile rather than a guessed one."""
    assert derive_kv_cache_profile({"num_hidden_layers": 48}) is None
    assert derive_kv_cache_profile({}) is None


def test_unknown_dtypes_default_to_half_precision() -> None:
    """An unrecognized dtype name is treated as a two-byte element."""
    assert kv_element_bytes_for_dtype("torch.bfloat16") == 2
    assert kv_element_bytes_for_dtype("float32") == 4
    assert kv_element_bytes_for_dtype("something-new") == 2
