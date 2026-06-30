"""Configuration contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


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
