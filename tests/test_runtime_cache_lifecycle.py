from types import SimpleNamespace

from lmrs.model_cache import CacheState, DiskModelCache
from lmrs.model_lifecycle import LifecycleState, ModelMemoryLifecycle
from lmrs.runtime_client import RuntimeClient


def test_disk_model_cache_reports_missing_model():
    cache = DiskModelCache(cache_root="/tmp/lmrs-test-cache")

    result = cache.status("missing-model")

    assert result.success is False
    assert result.status == CacheState.NOT_CACHED
    assert result.reason_code == "MODEL_NOT_CACHED"


def test_model_lifecycle_load_status_and_unload():
    lifecycle = ModelMemoryLifecycle()

    loaded = lifecycle.load_model("model-a")
    status = lifecycle.model_status("model-a")
    unloaded = lifecycle.unload_model("model-a")

    assert loaded.success is True
    assert loaded.state == LifecycleState.LOADED
    assert status.success is True
    assert status.state == LifecycleState.LOADED
    assert unloaded.success is True
    assert unloaded.state == LifecycleState.NOT_LOADED


def test_runtime_client_normalizes_mapping_result():
    request = SimpleNamespace(request_id="req-1")
    profile = {
        "executor": lambda _request: {
            "assistant_message": "hello",
            "usage": {"tokens": 3},
            "metadata": {"source": "test"},
        }
    }

    result = RuntimeClient(backend="unit").execute(request, profile)

    assert result.status == "ok"
    assert result.assistant_message == "hello"
    assert result.usage == {"tokens": 3}
    assert result.metadata == {"source": "test"}


def test_runtime_client_reports_missing_executor():
    request = SimpleNamespace(request_id="req-2")

    result = RuntimeClient(backend="unit").execute(request, object())

    assert result.reason_code == "RUNTIME_EXECUTOR_UNAVAILABLE"
    assert result.retriable is False
