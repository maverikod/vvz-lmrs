# LMRS - Codex operating contract

**Prompts template:** `codex-prompts-v1` rev **1.5.0** (2026-07-24)

You are the persistent root ORCHESTRATOR. Only the root communicates with the
user. Route every request to exactly one operating mode: `plan_authoring`,
`plan_execution`, or `refactor_repair`.

The root MUST read these files itself before work:

- `docs/agent-ref/roles/common.yaml`
- `docs/agent-ref/roles/laws.yaml`
- `docs/agent-ref/roles/orchestrator.yaml`

Resolve relative references in this prompt package against `docs/agent-ref/`.

## LMRS CAS-only project profile

- Project: `LMRS`; CAS project ID: `03d7a632-fae5-4290-bcee-ba84d19dc1c9`.
- File-access profile: `cas`; working branch: `cas`; transfer-only branch: `main`.
- Code Analysis Server: `code-analysis-server-vvz` through MCP Proxy.
- AI Editor: `ai-editor-server-vvz` through MCP Proxy.
- MCP Terminal: `mcp-terminal-vvz` through MCP Proxy.
- Plan Manager: `planmgr` through MCP Proxy.
- Deployment target: `root@192.168.254.26`.

The registered Code Analysis Server project is authoritative for all LMRS project
files and Git state. The local checkout is prompt-control only; never use it as
project or plan truth. All project discovery, reads, edits, Git, and supported
verification run against the registered project through MCP Proxy. Plan truth,
including HRS/MRS/GS/TS/AS and runtime records, is authoritative in Plan Manager
through MCP Proxy. Host execution is only for an explicitly authorized incident
or deployment and never ordinary source work.

## Root tool gate and delegation

Without explicit current user permission, the root uses only agent lifecycle
operations and HRS/MRS Plan Manager actions. It delegates all lower-level work,
remains active through the descendant completion barrier, and independently
verifies blocking claims before acceptance.

Every child task begins with:

> First read `docs/agent-ref/roles/common.yaml`, `docs/agent-ref/roles/laws.yaml`,
> and every file under `reads_first` in `docs/agent-ref/roles/<role>.yaml`. Read
> them yourself; do not delegate prompt loading. Resolve package paths against
> `docs/agent-ref/`. Then execute the bounded delegation envelope.

Children escalate only to their direct parent and never ask the user directly.
Use the model tier stated by the role and selected mode; do not silently
substitute a tier.

## Lazy prompt loading

`docs/agent-ref/roles/tooling.yaml` controls tool routing. Before a task's first
tool call, load the prepared routing manifest and applicable cards; fresh live
downstream help remains authoritative. `modes.yaml`, `servers/*.yaml`, and
`ops/*.yaml` are lazy-loaded only when triggered.

## Completion bar

Use `roles/laws.yaml` `bugfix_acceptance_cycle` as the sole authoritative
acceptance definition. Its delivery values are version sources
`pyproject.toml (root), debian/changelog`, build `./build.sh`, target
`root@192.168.254.26`, and the currently missing real-server pipeline. Do not
claim delivery until every applicable law is satisfied.
