"""Tests pinning the exhaustive command documentation to the live surface.

The paradigm mirrors planmgr and the code-analysis server: every registered
command must document itself completely - detailed description, parameters,
return value, usage examples, error cases keyed by stable reason codes, best
practices - and the info command must publish the whole of it plus the service
guide. A new command cannot ship undocumented: these tests fail on the gap.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from lmrs.adapter.command_docs import COMMAND_DOCS, SERVICE_GUIDE
from lmrs.adapter.registration import LMRS_PUBLIC_COMMAND_CLASSES
from lmrs.adapter import info as info_module

_MANDATORY_DOC_KEYS = {
    "category",
    "detailed_description",
    "parameters",
    "return_value",
    "usage_examples",
    "error_cases",
    "best_practices",
}

_MANDATORY_METADATA_KEYS = _MANDATORY_DOC_KEYS | {"name", "summary", "type", "version", "author", "email"}


def _registered_names() -> set[str]:
    """Return every registered public command name."""
    return {command.name for command in LMRS_PUBLIC_COMMAND_CLASSES}


def test_every_registered_command_is_documented() -> None:
    """The documentation set and the command surface are the same set."""
    assert set(COMMAND_DOCS) == _registered_names(), {
        "documented but not registered": sorted(set(COMMAND_DOCS) - _registered_names()),
        "registered but undocumented": sorted(_registered_names() - set(COMMAND_DOCS)),
    }


def test_every_documentation_entry_is_complete() -> None:
    """Every entry carries every mandatory block, non-degenerate."""
    for name, entry in COMMAND_DOCS.items():
        missing = _MANDATORY_DOC_KEYS - set(entry)
        assert not missing, f"{name} lacks documentation blocks: {sorted(missing)}"
        assert len(entry["detailed_description"]) > 80, f"{name} detailed_description is too thin"
        assert entry["usage_examples"], f"{name} has no usage examples"
        assert entry["return_value"], f"{name} has no return-value documentation"


def test_metadata_assembles_the_full_paradigm() -> None:
    """Every command class returns the complete fleet-paradigm metadata."""
    for command_class in LMRS_PUBLIC_COMMAND_CLASSES:
        metadata = command_class.metadata()
        missing = _MANDATORY_METADATA_KEYS - set(metadata)
        assert not missing, f"{command_class.name} metadata lacks: {sorted(missing)}"
        assert metadata["name"] == command_class.name
        assert metadata["type"] == "custom"


def test_documented_parameters_match_the_schema() -> None:
    """A parameter documented for a command exists in its schema, and every
    schema property of a parameterized command is documented."""
    for command_class in LMRS_PUBLIC_COMMAND_CLASSES:
        entry = COMMAND_DOCS[command_class.name]
        schema_properties = set(command_class.get_schema().get("properties", {}))
        documented = set(entry["parameters"])
        assert documented <= schema_properties or not schema_properties, (
            f"{command_class.name} documents parameters absent from its schema: "
            f"{sorted(documented - schema_properties)}"
        )
        assert schema_properties <= documented or not documented, (
            f"{command_class.name} has undocumented schema properties: "
            f"{sorted(schema_properties - documented)}"
        )


def test_error_cases_follow_the_paradigm_shape() -> None:
    """Every error case carries description, message and solution."""
    for name, entry in COMMAND_DOCS.items():
        for code, case in entry["error_cases"].items():
            assert set(case) == {"description", "message", "solution"}, f"{name}/{code} is malformed"


def test_the_service_guide_names_every_command_exactly_once() -> None:
    """The command-family overview covers the surface with no strays."""
    listed = [name for family in SERVICE_GUIDE["command_families"].values() for name in family]

    assert sorted(listed) == sorted(_registered_names())
    assert len(listed) == len(set(listed))


def test_info_publishes_the_guide_and_full_command_docs(monkeypatch) -> None:
    """The info payload carries the guide and complete metadata per command."""
    monkeypatch.setattr(info_module, "_registration_snapshot", lambda: {"enabled": False, "registered": False})

    payload = info_module.build_info_payload(None)

    assert payload["documentation"]["purpose"].startswith("LMRS")
    assert "invariant" in payload["documentation"]
    entries = [entry for family in payload["capabilities"].values() for entry in family]
    assert {entry["name"] for entry in entries} == _registered_names()
    for entry in entries:
        assert entry["schema"] is not None, f"{entry['name']} has no schema in info"
        metadata = entry["metadata"]
        missing = _MANDATORY_METADATA_KEYS - set(metadata)
        assert not missing, f"{entry['name']} info metadata lacks: {sorted(missing)}"
