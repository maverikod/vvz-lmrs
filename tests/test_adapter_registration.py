import asyncio

from lmrs.adapter.registration import (
    ChatCommand,
    HealthcheckCommand,
    LMRS_PUBLIC_COMMAND_CLASSES,
    LocalModelCacheStatusCommand,
    LocalModelLoadCommand,
    register_lmrs_commands,
)
from lmrs.commands import CommandName
from lmrs.contracts import build_default_service_boundary
from mcp_proxy_adapter.commands.command_registry import CommandRegistry


def test_register_lmrs_commands_adds_public_command_classes():
    registry = CommandRegistry()

    register_lmrs_commands(registry)

    assert CommandName.HEALTHCHECK in registry._commands
    assert CommandName.LOCAL_MODEL_CACHE_STATUS in registry._commands
    assert CommandName.LOCAL_MODEL_LOAD in registry._commands


def test_adapter_command_schema_requires_model_name():
    schema = LocalModelLoadCommand.get_schema()

    assert schema["required"] == ["model_name"]
    assert schema["properties"]["allow_preload"]["default"] is False


def test_chat_command_schema_accepts_message_and_model():
    schema = ChatCommand.get_schema()

    assert schema["required"] == ["message", "model_name"]
    assert schema["properties"]["max_tokens"]["default"] == 128


def test_healthcheck_command_returns_success_result():
    result = asyncio.run(HealthcheckCommand().execute())

    payload = result.to_dict()
    assert payload["success"] is True
    assert payload["data"]["command"] == CommandName.HEALTHCHECK
    assert payload["data"]["payload"]["status"] == "ok"


def test_cache_status_command_returns_structured_missing_model():
    result = asyncio.run(
        LocalModelCacheStatusCommand().execute(model_name="missing-model")
    )

    payload = result.to_dict()
    assert payload["success"] is True
    assert payload["data"]["command"] == CommandName.LOCAL_MODEL_CACHE_STATUS
    assert payload["data"]["payload"]["reason_code"] == "MODEL_NOT_CACHED"


def test_service_boundary_command_surface_matches_registered_commands():
    boundary = build_default_service_boundary()
    registered_names = tuple(command.name for command in LMRS_PUBLIC_COMMAND_CLASSES)

    assert boundary.adapter_exposure.command_surface == registered_names
    assert boundary.mvp_scope.includes("local_model_disk_cache")
    assert boundary.mvp_scope.includes("model_memory_lifecycle")
