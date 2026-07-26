# Codex project prompt template

This archive provides a thin-core, lazy-loaded orchestration contract for Codex.
Place `AGENTS.md` at the project root and keep `modes.yaml`, `roles/`, `servers/`,
and `ops/` together under `docs/agent-ref/`.

Codex has no prompt-file import directive. The root reads `roles/common.yaml`,
`roles/laws.yaml`, and `roles/orchestrator.yaml` explicitly. Every child reads
the common files plus its own role file before acting.

## Required substitutions

- `LMRS`: project name
- `03d7a632-fae5-4290-bcee-ba84d19dc1c9`: registered CAS project UUID
- `/home/vasilyvz/projects/tools/lmrs`: local checkout path
- `code-analysis-server-vvz`, `ai-editor-server-vvz`, `mcp-terminal-vvz`,
  `planmgr`: live MCP Proxy registrations
- `root@192.168.254.26`: authorized deployment target
- `./build.sh`: canonical build/release entrypoint
- `TODO(user): no real-server pipeline script exists yet`: the single real-server acceptance pipeline
- `pyproject.toml (root), debian/changelog`: authoritative version file; add further lockstep files
  to `ops/delivery-release.yaml` when required
- `none`: the part of `root@192.168.254.26` the
  bugfix-cycle deploy step (`roles/laws.yaml` `bugfix_acceptance_cycle`) must
  never touch even though the rest of the host is a test target (e.g. "the
  doc-store project's database"); set to `none` when nothing is excluded

## Model mapping

- Root and HRS/MRS: `gpt-5.6-sol`, `max`
- GS: `gpt-5.6-terra`, `xhigh`
- TS: `gpt-5.6-terra`, `medium`
- AS: `gpt-5.6-luna`, `medium`
- Refactor/repair researcher, executor, tester: `gpt-5.5`, `medium`
- Delivery mechanics (deliverer, `docs/agent-ref/roles/deliverer.yaml`): `gpt-5.5`, `medium`

Never silently substitute a tier. Every non-leaf parent owns context formation,
child dispatch, upward escalation, and the complete descendant barrier.

## Acceptance laws

Use exactly one mode per branch: `plan_authoring`, `plan_execution`, or
`refactor_repair`. Treat child reports as untrusted claims. Verify artifacts,
tests, live behavior, and authoritative server state independently.

Long operations may validly enter a queue. Configure adapter clients explicitly
for synchronous poll-and-unwrap or asynchronous/message handling. Queue handoff
alone is not a defect.

Keep one real-server pipeline and extend it with regression scenarios. Build and
verify from the active working branch. Merge into transfer-only `main` only after
production acceptance. The agent reports the ready commit and waits for the user
to push before synchronizing the opposite site. Delivery mechanics may be
delegated to `docs/agent-ref/roles/deliverer.yaml` under an explicit orchestrator delivery
decision — the deliverer never decides whether or where to deploy/repair, it only
executes the mandated procedure.

**Build execution locus (HARD RULE, default local profile):** `./build.sh`
(and every other build/test/deploy script) runs on the LOCAL host from the LOCAL
checkout via local shell — never through MCP Proxy or the MCP Terminal
sandbox/host-exec path. Only the deploy step touches `root@192.168.254.26`. See
`docs/agent-ref/roles/laws.yaml` `cas_mode` / `host_execution` and
`docs/agent-ref/ops/delivery-release.yaml` `build_execution`.

## Validation

Parse all YAML, verify every referenced package file exists, and ensure the only
remaining `{{...}}` tokens are the approved substitutions listed above. Confirm
live server IDs and command schemas through MCP Proxy before first use.

## Versioning

This archive carries a canonical version marker in three places: the top-level
`VERSION` file; a visible plain-text marker line right under the H1 in
`AGENTS.md`, formatted exactly `**Prompts template:** \`codex-prompts-v1\` rev
**<version>** (<date>)` (plain text, not a `#` line, so it never reads as a
second Markdown H1 and stays visible in rendered Markdown — an HTML comment
would not); and a `# prompts-template: codex-prompts-v1 rev <version>
(<date>)` YAML-comment header line in `roles/laws.yaml`. Bump the **minor**
version on any change to a law or to file/section content (added, removed, or
materially reworded laws, roles, or ops procedures); bump the **patch**
version on wording-only fixes that change no behavior (typos, clarity edits,
comment fixes). Bump **major** only on a breaking architecture change to the
template itself. Projects stamped from this template record the exact rev
they were stamped from (in their own `AGENTS.md` and `roles/laws.yaml` header
lines) so drift against a later template revision can be detected by diffing
against the matching tagged archive.

Archive naming follows the convention `<lineage>-v<semver>.tar.gz` (lineage
`codex-prompts-v1` for this template), e.g. `codex-prompts-v1.1.0.tar.gz`.
Every version bump produces a NEW archive file under the new name — the old
one is not overwritten in place. The filename semver, the `VERSION` file
content, and the visible marker line in `AGENTS.md` MUST always agree.
