"""Command schema and metadata contracts for the LMRS adapter surface.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


JsonSchema = Mapping[str, Any]


@dataclass(frozen=True)
class CommandSchemaContract:
    """Per-command request/result/error schema provider contract."""

    command_name: str
    request_schema: JsonSchema = field(default_factory=dict)
    result_schema: JsonSchema = field(default_factory=dict)
    error_schema: JsonSchema = field(default_factory=dict)

    def get_schema(self) -> JsonSchema:
        """Return the request parameter schema for the command."""
        return self.request_schema

    def get_result_schema(self) -> JsonSchema:
        """Return the result schema for the command."""
        return self.result_schema

    def get_error_schema(self) -> JsonSchema:
        """Return the error schema for the command."""
        return self.error_schema


@dataclass(frozen=True)
class CommandMetadataContract:
    """Per-command discovery metadata for MCP help, proxy discovery, OpenAPI."""

    command_name: str
    description: str
    parameter_schema: JsonSchema = field(default_factory=dict)
    result_schema: JsonSchema = field(default_factory=dict)
    error_schema: JsonSchema = field(default_factory=dict)
    stable_error_codes: tuple[str, ...] = ()
    examples: tuple[Mapping[str, Any], ...] = ()
    best_practices: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return adapter-help-compatible metadata as a plain mapping."""
        return {
            "name": self.command_name,
            "description": self.description,
            "parameters": dict(self.parameter_schema),
            "result_schema": dict(self.result_schema),
            "error_schema": dict(self.error_schema),
            "stable_error_codes": list(self.stable_error_codes),
            "examples": [dict(example) for example in self.examples],
            "best_practices": list(self.best_practices),
            "metadata": dict(self.metadata),
        }


def _tuple_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    return tuple(item for item in value if isinstance(item, Mapping))


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def build_command_metadata(
    schema_contract: CommandSchemaContract,
    command_spec: Mapping[str, Any],
) -> CommandMetadataContract:
    """Assemble CommandMetadataContract without reading executor internals."""
    command_name = str(command_spec.get("command_name") or schema_contract.command_name)
    description = str(command_spec.get("description", ""))
    metadata = command_spec.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise TypeError("command_spec metadata must be a mapping")
    return CommandMetadataContract(
        command_name=command_name,
        description=description,
        parameter_schema=schema_contract.get_schema(),
        result_schema=schema_contract.get_result_schema(),
        error_schema=schema_contract.get_error_schema(),
        stable_error_codes=_tuple_of_strings(command_spec.get("stable_error_codes")),
        examples=_tuple_of_mappings(command_spec.get("examples")),
        best_practices=_tuple_of_strings(command_spec.get("best_practices")),
        metadata=dict(metadata),
    )
