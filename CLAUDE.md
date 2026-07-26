# lmrs - Claude operating contract

**Prompts template:** `claude-prompts-v1` rev **1.6.3** (2026-07-26)

This file is the Claude entrypoint and is loaded automatically at session start.
Read these files yourself before the first action of a task:

- `claude/roles/common.yaml`
- `claude/roles/laws.yaml`
- `claude/roles/tooling.yaml`
- `claude/roles/orchestrator.yaml`

Do not delegate reading or interpretation of those files to a subagent. Resolve
every relative prompt-package reference against `claude/`.

## Project profile

- Project: `lmrs`
- Local checkout: `/home/testuser/projects/lmrs`
- CAS project ID: `03d7a632-fae5-4290-bcee-ba84d19dc1c9`
- CAS server: `code-analysis-server-vvz`

## Operating model

Role files are workflow stages executed by this session, not spawned agents.
Sequence implementation-heavy work as `researcher` -> `context_former` ->
`coder` -> `tester` -> `conscience`, and declare the working mode
(`planning`, `analysis`, `refactoring`) from `claude/modes.yaml`.

## Claude specifics

- A read-only research subagent may be used for broad search fan-out; `coder`,
  `tester`, and `conscience` stay in this session.
- A subagent prompt must restate the laws it needs; subagents do not inherit this file.
- Keep the plan visible with the task/todo tools on multi-step work, and re-read
  contract files after context compaction instead of recalling them.
- `.claude/` is Claude Code harness configuration, not part of this bundle.

Use the `claude/` bundle as the authoritative Claude contract for this project.
Do not read Codex prompt files unless the task explicitly requires cross-checking
them.
