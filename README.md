# LMRS — Local Model Runtime Service

LMRS is a Python service that manages the lifecycle, scheduling, admission,
and capacity of locally hosted language models, exposing them through a thin
command layer and an MCP proxy adapter.

> Status: under active development (contracts-first). The public command and
> adapter contracts are being implemented module by module.

## Requirements

- Python >= 3.12

## Installation

Editable install for development:

```
pip install -e .
```

The base install depends only on PyYAML. To add the MCP proxy server stack
(FastAPI / Starlette / Twisted / etc. via `mcp-proxy-adapter`):

```
pip install -e '.[server]'
```

## Package layout

- `lmrs/` — the package
  - `adapter/` — MCP proxy adapter runtime, registration, schemas
  - `cli/` — command-line operator surface
  - `proxy/` — proxy lifecycle and registration
  - top-level modules: `admission`, `calibration`, `commands`,
    `configuration`, `contracts`, `estimation`, `gateway`, `lmcache`,
    `model_cache`, `model_lifecycle`, `queue`, `runtime_client`,
    `telemetry`, `vram`
- `tools/` — maintenance and plan tooling (uses PyYAML)
- `docs/` — project documentation

## License

See [LICENSE](LICENSE).

## Author

Vasiliy Zdanovskiy <vasilyvz@gmail.com>
