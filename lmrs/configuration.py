"""Configuration contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class HardwareProfile:
    """Hardware capacity profile used by LMRS admission and calibration logic.

    Attributes:
        gpu_count: Number of GPUs in this hardware configuration.
        total_vram_bytes: Total VRAM available across all GPUs in bytes.
        total_ram_bytes: Total system RAM in bytes.
        gpu_model: Optional GPU model identifier string.
        metadata: Arbitrary hardware metadata.
    """

    gpu_count: int = 1
    total_vram_bytes: int = 24 * 1024**3
    total_ram_bytes: int = 128 * 1024**3
    gpu_model: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KVCacheProfile:
    """Model KV-cache parameters used to derive memory facts.

    Attributes:
        layers: Number of transformer layers.
        kv_heads: Number of KV attention heads.
        head_dim: Dimension of each attention head.
        kv_element_bytes: Bytes per KV element.
        declared_context_window: Declared maximum context window in tokens.
        runtime_kv_dtype: Runtime KV cache data type identifier.
        quantization_profile: Optional quantization profile identifier.
    """

    layers: int
    kv_heads: int
    head_dim: int
    kv_element_bytes: int
    declared_context_window: int
    runtime_kv_dtype: str = "fp16"
    quantization_profile: str | None = None

    def kv_bytes_per_token(self) -> int:
        """Return worst-case KV-cache bytes required for one token.

        Returns:
            KV-cache bytes per token from layers, heads, head_dim, and element size.
        """
        return self.layers * 2 * self.kv_heads * self.head_dim * self.kv_element_bytes

    def kv_full_window_bytes(self) -> int:
        """Return worst-case KV-cache bytes for the declared context window.

        Returns:
            Total KV-cache bytes for the full declared context window.
        """
        return self.declared_context_window * self.kv_bytes_per_token()


@dataclass(frozen=True)
class ModelProfile:
    """Runtime-independent profile for one locally executable model.

    Attributes:
        model_name: Name of the local model.
        runtime_backend: Runtime backend identifier.
        model_path_or_endpoint: Local path or endpoint for this model.
        declared_context_window: Declared maximum context window in tokens.
        tokenizer_profile: Tokenizer profile identifier.
        load_policy: Model loading policy identifier.
        kv_cache_profile: KV-cache memory profile for this model.
        concurrency_policy: Concurrency limits per policy key.
        options: Additional model-specific options.
    """

    model_name: str
    runtime_backend: str
    model_path_or_endpoint: str
    declared_context_window: int
    tokenizer_profile: str
    load_policy: str
    kv_cache_profile: KVCacheProfile
    concurrency_policy: Mapping[str, int] = field(default_factory=dict)
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Configuration-owned LMRS profile facts consumed by capacity logic.

    Attributes:
        hardware_profile: Hardware capacity profile for admission.
        model_profiles: Mapping from model name to its profile.
        default_model_name: Name of the default model.
        resident_services: Names of always-running GPU resident services.
        safety_margin_bytes: VRAM reserved as safety margin in bytes.
        runtime_reserve_bytes: VRAM reserved for runtime overhead in bytes.
        queue_policy: Queue scheduling policy identifier.
        queue_limits: Queue capacity limits per limit key.
    """

    hardware_profile: HardwareProfile
    model_profiles: Mapping[str, ModelProfile]
    default_model_name: str
    resident_services: tuple[str, ...] = ()
    safety_margin_bytes: int = 0
    runtime_reserve_bytes: int = 0
    queue_policy: str = "largest_fit"
    queue_limits: Mapping[str, int] = field(default_factory=dict)


_DTYPE_ELEMENT_BYTES: Mapping[str, int] = {
    "float32": 4,
    "float": 4,
    "fp32": 4,
    "float16": 2,
    "half": 2,
    "fp16": 2,
    "bfloat16": 2,
    "bf16": 2,
    "float8": 1,
    "fp8": 1,
    "fp8_e4m3": 1,
    "fp8_e5m2": 1,
    "float8_e4m3fn": 1,
    "float8_e5m2": 1,
    "int8": 1,
}


def kv_element_bytes_for_dtype(dtype_name: str) -> int:
    """Return the bytes one KV-cache element occupies for a dtype.

    Args:
        dtype_name: Runtime or model dtype name, for example ``bfloat16``.

    Returns:
        The element size in bytes, defaulting to two bytes for an unrecognized
        name, since a half-precision KV cache is what every supported runtime
        uses unless it is explicitly told otherwise.
    """
    normalized = dtype_name.strip().lower().removeprefix("torch.")
    return _DTYPE_ELEMENT_BYTES.get(normalized, 2)


def derive_kv_cache_profile(
    model_config: Mapping[str, Any],
    kv_cache_dtype: str | None = None,
) -> KVCacheProfile | None:
    """Derive KV-cache facts from a model's own configuration file.

    The parameters come from the cached ``config.json`` of the model the runtime
    will actually load, so the KV cost is computed from that model rather than
    from a configured guess. Weight quantization is deliberately ignored: it
    reduces weight bytes, not KV-cache bytes, unless the runtime is explicitly
    told to keep the KV cache in a smaller dtype, which is what
    ``kv_cache_dtype`` expresses.

    Args:
        model_config: Parsed ``config.json`` of the cached model.
        kv_cache_dtype: KV-cache dtype the runtime was configured with; the
            model dtype applies when it is absent or ``auto``.

    Returns:
        A KVCacheProfile, or None when the configuration does not state the
        parameters the formula needs.
    """
    text_config = model_config.get("text_config")
    if isinstance(text_config, Mapping):
        merged: dict[str, Any] = {**dict(model_config), **dict(text_config)}
    else:
        merged = dict(model_config)
    layers = merged.get("num_hidden_layers") or merged.get("n_layer")
    attention_heads = merged.get("num_attention_heads") or merged.get("n_head")
    kv_heads = merged.get("num_key_value_heads") or attention_heads
    head_dim = merged.get("head_dim")
    hidden_size = merged.get("hidden_size") or merged.get("n_embd")
    if not isinstance(head_dim, int) or head_dim <= 0:
        if isinstance(hidden_size, int) and isinstance(attention_heads, int) and attention_heads > 0:
            head_dim = hidden_size // attention_heads
        else:
            head_dim = 0
    context_window = 0
    for key in ("max_position_embeddings", "max_sequence_length", "n_positions", "model_max_length"):
        value = merged.get(key)
        if isinstance(value, int) and value > 0:
            context_window = value
            break
    if not isinstance(layers, int) or layers <= 0:
        return None
    if not isinstance(kv_heads, int) or kv_heads <= 0:
        return None
    if head_dim <= 0 or context_window <= 0:
        return None
    model_dtype = merged.get("torch_dtype") or merged.get("dtype") or "bfloat16"
    requested = kv_cache_dtype if kv_cache_dtype and kv_cache_dtype.lower() != "auto" else str(model_dtype)
    return KVCacheProfile(
        layers=layers,
        kv_heads=kv_heads,
        head_dim=head_dim,
        kv_element_bytes=kv_element_bytes_for_dtype(str(requested)),
        declared_context_window=context_window,
        runtime_kv_dtype=str(requested),
        quantization_profile=str(merged.get("quantization_config", {}).get("quant_method")) if isinstance(merged.get("quantization_config"), Mapping) else None,
    )


def validate_runtime_configuration(
    config: RuntimeConfiguration,
) -> RuntimeConfiguration:
    """Validate configuration-owned profile facts and return the same config.

    Args:
        config: The RuntimeConfiguration to validate.

    Returns:
        The same RuntimeConfiguration object if all validations pass.
    """
    hardware = config.hardware_profile
    if hardware.gpu_count < 1:
        raise ValueError("hardware_profile.gpu_count must be at least 1")
    if hardware.total_vram_bytes <= 0:
        raise ValueError("hardware_profile.total_vram_bytes must be positive")
    if hardware.total_ram_bytes <= 0:
        raise ValueError("hardware_profile.total_ram_bytes must be positive")
    if not config.model_profiles:
        raise ValueError("model_profiles must not be empty")
    if config.default_model_name not in config.model_profiles:
        raise ValueError("default_model_name must reference a configured model")
    if config.safety_margin_bytes < 0:
        raise ValueError("safety_margin_bytes must be non-negative")
    if config.runtime_reserve_bytes < 0:
        raise ValueError("runtime_reserve_bytes must be non-negative")
    for model_name, model in config.model_profiles.items():
        if model.model_name != model_name:
            raise ValueError("model_profiles keys must match model_name")
        if model.declared_context_window <= 0:
            raise ValueError("model declared_context_window must be positive")
        kv_profile = model.kv_cache_profile
        if (
            kv_profile.layers <= 0
            or kv_profile.kv_heads <= 0
            or kv_profile.head_dim <= 0
        ):
            raise ValueError("kv cache dimensions must be positive")
        if kv_profile.kv_element_bytes <= 0:
            raise ValueError("kv_element_bytes must be positive")
        if kv_profile.declared_context_window != model.declared_context_window:
            raise ValueError(
                "kv profile context window must match"
                " model context window"
            )
    return config
