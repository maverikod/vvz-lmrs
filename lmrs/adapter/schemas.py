"""Command schema and metadata contracts for the LMRS adapter surface.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class CommandSchemaContract:
    """Per-command request/result/error schema provider contract.

    Declares three schema providers used by adapter schema-based validation
    before domain checks: a request schema (get_schema), a result schema
    (get_result_schema), and an error schema (get_error_schema). The schemas
    live separately from executor code.

    Attributes:
        command_name: Public command name these schemas describe.
    """

    command_name: str = ""

    def get_schema(self) -> Mapping[str, Any]:
        """Return the request parameter schema for the command.

        Returns:
            A JSON-schema-like mapping describing request parameters.
        """
        raise NotImplementedError(
            "CommandSchemaContract.get_schema is a contract stub"
        )

    def get_result_schema(self) -> Mapping[str, Any]:
        """Return the result schema for the command.

        Returns:
            A JSON-schema-like mapping describing the result shape.
        """
        raise NotImplementedError(
            "CommandSchemaContract.get_result_schema is a contract stub"
        )

    def get_error_schema(self) -> Mapping[str, Any]:
        """Return the error schema for the command.

        Returns:
            A JSON-schema-like mapping describing the error shape.
        """
        raise NotImplementedError(
            "CommandSchemaContract.get_error_schema is a contract stub"
        )


@dataclass(frozen=True)
class CommandMetadataContract:
    """Per-command discovery metadata for MCP help, proxy discovery, OpenAPI.

    Publishes help, schema, error, and example information sufficient for MCP
    help, proxy discovery, and OpenAPI generation without reading internal
    implementation. Metadata lives separately from executor code.

    Attributes:
        command_name: Public command name.
        description: Human-readable command description.
        parameter_schema: Request parameter schema mapping.
        result_schema: Result schema mapping.
        error_schema: Error schema mapping.
        stable_error_codes: Stable machine-readable error codes for the command.
        examples: Usage examples for the command.
        best_practices: Best-practice notes for the command.
        metadata: Arbitrary additional metadata.
    """

    command_name: str
    description: str
    parameter_schema: Mapping[str, Any] = field(default_factory=dict)
    result_schema: Mapping[str, Any] = field(default_factory=dict)
    error_schema: Mapping[str, Any] = field(default_factory=dict)
    stable_error_codes: tuple[str, ...] = ()
    examples: tuple[Mapping[str, Any], ...] = ()
    best_practices: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


def build_command_metadata(
    schema_contract: CommandSchemaContract,
    command_spec: Mapping[str, Any],
) -> CommandMetadataContract:
    """Assemble a CommandMetadataContract from a schema contract.

    Populates description, parameter/result/error schemas, stable error codes,
    examples, and best practices so the result is sufficient for MCP help, proxy
    discovery, and OpenAPI generation without reading executor internals.

    Args:
        schema_contract: The per-command schema provider.
        command_spec: The public command specification mapping.

    Returns:
        A populated CommandMetadataContract for the command.
    """
    raise NotImplementedError(
        "build_command_metadata is a contract stub"
    )
