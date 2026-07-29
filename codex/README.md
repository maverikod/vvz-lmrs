# lmrs Codex prompt package

`../AGENTS.md` is the entrypoint. This directory contains the project-bound
Codex contract bundle.

Package version: `v1.6.2`

## Layout

- `modes.yaml`: mode router.
- `roles/common.yaml`, `roles/laws.yaml`, `roles/orchestrator.yaml`: mandatory core read.
- `roles/*.yaml`: stage contracts.
- `ops/*.yaml`: lazily loaded operating cards, including cheapest-capable model selection.
- `VERSION`: bundle version marker.

## Project bindings

- Project: `lmrs`
- Local checkout: `/home/testuser/projects/lmrs`
- CAS project ID: `03d7a632-fae5-4290-bcee-ba84d19dc1c9`
- CAS server: `code-analysis-server-vvz`

## Notes

- This bundle is Codex-only.
- Claude prompt files remain outside this directory and are not modified by it.
- Relative bundle references resolve from `codex/`.
