"""Tests pinning the generated configuration against the adapter's validator.

The 2026-08-13 incident: mcp-proxy-adapter 8.10.25 made ``create_and_run_server``
validate the full configuration schema (``transport``, ``proxy_registration``,
``debug``, ``security``, ``roles`` and extended ``logging``/``commands`` keys)
while its ``SimpleConfigGenerator`` still emits the legacy document, so a config
that the generator produced crash-looped the deployed container. These tests pin
that every document ``LmrsConfigGenerator`` writes passes the same validator the
server runs at startup, and that the stub sections do not enable a second
registration path.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_proxy_adapter.core.validation.config_validator import ConfigValidator

from lmrs.config.generator import LmrsConfigGenerator


def _error_results(config_path: Path) -> list:
    """Run the adapter's startup validator and keep only error-level results."""
    validator = ConfigValidator(str(config_path))
    validator.load_config()
    results = validator.validate_config()
    return [result for result in results if getattr(result.level, "value", result.level) == "error"]


def _write_cert_material(directory: Path) -> dict[str, str]:
    """Create placeholder certificate files so file-existence checks pass."""
    paths = {}
    for name in ("lmrs.crt", "lmrs.key", "ca.crt", "lmrs-client.crt", "lmrs-client.key"):
        target = directory / name
        target.write_text("placeholder\n", encoding="utf-8")
        paths[name] = str(target)
    return paths


def test_generated_http_config_passes_the_startup_validator(tmp_path: Path) -> None:
    """An http document must carry every section the startup validator requires."""
    out = tmp_path / "config.json"
    LmrsConfigGenerator().generate(str(out), protocol="http", log_dir=str(tmp_path))

    errors = _error_results(out)
    assert errors == [], f"generated config fails startup validation: {errors}"


def test_generated_https_config_passes_the_startup_validator(tmp_path: Path) -> None:
    """An https document with certificates must pass the startup validator."""
    certs = _write_cert_material(tmp_path)
    out = tmp_path / "config.json"
    LmrsConfigGenerator().generate(
        str(out),
        protocol="https",
        cert_file=certs["lmrs.crt"],
        key_file=certs["lmrs.key"],
        ca_cert_file=certs["ca.crt"],
        log_dir=str(tmp_path),
        with_proxy=True,
        registration_host="proxy.example.org",
        registration_port=3004,
        registration_protocol="https",
        registration_cert_file=certs["lmrs-client.crt"],
        registration_key_file=certs["lmrs-client.key"],
        registration_ca_file=certs["ca.crt"],
        server_id="lmrs",
    )

    errors = _error_results(out)
    assert errors == [], f"generated config fails startup validation: {errors}"


def test_the_stub_sections_do_not_enable_a_second_registration_path(tmp_path: Path) -> None:
    """Proxy registration runs off the legacy section; the stub must stay off."""
    out = tmp_path / "config.json"
    LmrsConfigGenerator().generate(str(out), protocol="http", log_dir=str(tmp_path))

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["proxy_registration"]["enabled"] is False
    assert document["proxy_registration"]["auto_register_on_startup"] is False
    assert document["security"]["enabled"] is False
    assert document["roles"]["enabled"] is False
