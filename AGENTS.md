# lmrs - Codex operating contract

**Prompts template:** `codex-prompts-v1` rev **1.6.12** (2026-07-31)

This file is the Codex entrypoint. The root must read these files itself at the
start of a task:

- `codex/roles/common.yaml`
- `codex/roles/laws.yaml`
- `codex/roles/orchestrator.yaml`

Do not delegate reading or interpretation of those files. Resolve every
relative prompt-package reference against `codex/`.

## Project profile

- Project: `lmrs`
- Local checkout: `/home/vasilyvz/projects/tools/lmrs`
- Default file-access profile: `local` (only the user may switch it).
- Deployment host: `root@192.168.254.26`.
- CAS project ID: `03d7a632-fae5-4290-bcee-ba84d19dc1c9`
- CAS server: `code-analysis-server-vvz`

Use the `codex/` bundle as the authoritative Codex contract for this project.
Do not read Claude-specific prompt files unless the task explicitly requires
cross-checking them.
