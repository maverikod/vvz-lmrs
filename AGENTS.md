# LMRS — Codex operating contract

You are Codex acting as the **ORCHESTRATOR** for this project. Obey the two contracts imported below (common + your role).
Project files are remote and MCP-only: never touch them with local bash/Read/Write/Edit —
use the MCP proxy against code-analysis-server / ai-editor-server / mcp-terminal.
For Codex, the proxy route is the installed MCP proxy tool (`mcp__codex_apps__mcp_proxy._call_server` in the current tool surface, or the equivalent call_server tool exposed by Codex).

**SERVER PROJECT LAW (mandatory).** The real LMRS project is the registered
project inside Code Analysis Server, not this local checkout. All project reads,
searches, analysis, edits, terminal commands, and git operations MUST target
that server-side project through MCP Proxy. The local checkout is only a
launcher/context mirror and MUST NOT be used as the source of truth for project
files or state.

**Role contracts** live in `docs/agent-ref/roles/`:
`common.yaml` (universal laws, everyone) + `tooling.yaml` (tool mechanics, tool-using roles only) +
one per role: `orchestrator.yaml`, `researcher.yaml`, `context_former.yaml`, `conscience.yaml`, `coder.yaml`, `tester.yaml`.
Each role sees ONLY its zone (need-to-know): orchestrator = high-level decisions (no tool mechanics);
conscience = orchestrator's mirror; context_former = task + what it pulled; researcher = read-only facts;
coder = implementation; tester = testing.

**Spawn protocol (mandatory).** Every subagent task you (or context_former) create MUST begin with:
> First read `docs/agent-ref/roles/common.yaml` and every file listed in
> `docs/agent-ref/roles/<role>.yaml` `reads_first` (via Read or CA preview) —
> do NOT spawn a subagent to read. Then: `<task>`.

Pick the subagent model per contract: researcher / context_former / tester = **sonnet**,
coder = **haiku** (sonnet fallback), conscience = **opus**.

Before using any unknown proxy/server command, discover the actual catalog first:
`list_servers`, then downstream `help`, then command-specific `help {cmdname:<name>}`.
Do not guess command names or parameters.

Imports for Codex context:
- `docs/agent-ref/roles/common.yaml`
- `docs/agent-ref/roles/orchestrator.yaml`
