# LMRS Coder — Editor Chain Reference

This is the single source of truth for how a coder edits files in the LMRS
project. All file access goes through the MCP proxy ONLY. The repository lives
on a remote machine; local bash / Read / Write / Edit do NOT reach the canonical
project and MUST NEVER be used.

## Servers and constants
- MCP tool: `mcp__claude_ai_MCP-Proxy__call_server`
- project_id: `03d7a632-fae5-4290-bcee-ba84d19dc1c9`
- Code Analysis server (CA): server_id `code-analysis-server`, copy_number 1
- AI Editor server: server_id `ai-editor-server`, copy_number 1
- All `file_path` values are project-relative (e.g. `lmrs/foo.py`), never host-absolute.

## Absolute rules
1. Edit/create files ONLY through the AI Editor server commands listed below.
2. NEVER use local bash, Read, Write, or Edit on project files. NEVER use `python`/`cat`/`sed` to write files.
3. On ANY tool error or unexpected response: STOP immediately and report the exact
   command name, the exact params you sent, and the exact error code/message.
   Do NOT improvise, do NOT fall back to bash, do NOT retry blindly.
4. NEVER use `# noqa` or `# type: ignore`, never weaken types to `Any` to pass a
   check, never write placeholder docstrings, never delete code to pass a gate.
5. Always `universal_file_close` the session at the end, even after an error.

## The editing chain (every file)
```
CA  session_create({comment})                       -> session_id
ED  universal_file_open(project_id, file_path, session_id[, create, initial_content])
ED  universal_file_preview(project_id, file_path, session_id[, node_ref])   # for modify
ED  universal_file_edit(project_id, session_id, operations)                 # for modify
ED  universal_file_write(project_id, session_id, file_path, write_mode="preview")
ED  universal_file_write(project_id, session_id, file_path, write_mode="commit")
ED  universal_file_close(project_id, session_id)
```
- `session_id` comes from CA `session_create` (param is `comment`, a short string — NOT project_id). The editor reuses this same id; `open` does NOT mint a new id.
- `universal_file_edit` mutates only the in-memory draft. Nothing reaches CA until a `commit`.

## Creating a NEW file (operation: create_file)
Use the simplest, most reliable path:
1. `session_create({comment})` -> session_id
2. `universal_file_open({project_id, file_path, session_id, create:true, initial_content:"<full file text>"})`
   - Pass the ENTIRE file content as the `initial_content` string, verbatim.
   - A create=true file is held locally only; the CA lock + registration happen on the FIRST commit.
3. `universal_file_write(..., write_mode:"preview")` -> inspect diff
4. `universal_file_write(..., write_mode:"commit")` -> must return `uploaded: true`
5. `universal_file_close(...)`

## Modifying an EXISTING file (operation: modify_file)
1. `session_create({comment})` -> session_id
2. `universal_file_open({project_id, file_path, session_id})`  (create defaults to false)
3. `universal_file_preview({project_id, file_path, session_id, node_ref:""})`
   - Returns top-level nodes. For `.py` each node has an integer `short_id`.
   - Find the node you want to anchor to (e.g. the last class) and note its `short_id`.
4. `universal_file_edit` with an operations batch (see operation shapes below).
5. `universal_file_write(..., write_mode:"preview")` -> inspect diff
6. `universal_file_write(..., write_mode:"commit")` -> must return `uploaded: true`
7. `universal_file_close(...)`

## Operation shapes
Python (.py / .pyi / .pyw) — node-based; `node_ref` int short_id from preview is
passed as a STRING. The server translates it to a CST node internally — this is
the normal path; there are no separate "CST commands".
- Insert after a sibling node (e.g. append a class/function after the last one):
  `{"type":"insert","target_node_id":"<short_id>","position":"after","code_lines":["\n","\n","def f() -> None:","    ..."]}`
  Use `code_lines` (list of strings, one per physical line) to avoid JSON escaping issues.
  The first elements may be empty strings "" to produce blank separator lines.
- Replace a node entirely:
  `{"type":"replace","node_id":"<short_id>","code_lines":[...]}`
- Insert at module level container: `{"type":"insert","parent_node_id":"__root__","position":"last","code_lines":[...]}`
- Do NOT put a parent node and its descendant in the SAME batch -> NESTED_BATCH_FORBIDDEN. Sibling targets are fine.

JSON / YAML (.json / .yaml / .yml) — tree-temp:
- Replace a scalar by JSON Pointer: `{"type":"replace","json_pointer":"/status","value":"done"}`
- Or by marked-tree int short_id (from preview): `{"type":"replace","node_ref":"<short_id>","value":"done"}`
- `value` accepts any JSON type (string, number, bool, null, array, object).

Text (.md / .txt / .rst):
- `{"type":"replace","node_ref":"<id>","content":"..."}` or line ranges `start_line`/`end_line` (1-based).
- `{"type":"insert","position":"last","content":"..."}` appends at end of file.

## Commit validation (Python)
`write_mode:"commit"` runs black-parseable + flake8 + mypy + docstring checks before
upload. On failure it returns `VALIDATION_ERROR` with the full error list and the
draft is unchanged. To fix: `universal_file_edit` again (re-`universal_file_preview`
first to get fresh short_ids), then `write_mode:"preview"`, then `write_mode:"commit"`.
Allow at most 2 fix attempts; if still failing, STOP and report the exact messages.

## Reading a file (without editing)
- Use `universal_file_preview` with project_id + file_path and NO session_id (one-shot read).
- For raw lines of YAML / Markdown / text you MAY use CA `get_file_lines`.
- DO NOT use CA `get_file_lines` on a healthy (parseable) `.py` file — CA rejects it
  with `USE_CST_COMMANDS`. That error means "use structural reads", NOT "infra broken".

## Quality gate (after a successful commit), on CA
- `lint_code` {project_id, file_path} -> success must be true (0 errors)
- `type_check_code` {project_id, file_path} -> success must be true (0 errors)
- `run_project_module` {project_id, module:"lmrs.<name>"} -> returncode must be 0
If any fails for a reason other than your own typo, STOP and report.

## Code conventions (LMRS)
- Module docstring includes: `Author: Vasiliy Zdanovskiy` and `email: vasilyvz@gmail.com`.
- `from __future__ import annotations` at the top of every `.py`.
- Every class has an `Attributes:` docstring section. Every function/method with
  params has `Args:` and `Returns:`; a no-param function has `Returns:` only.
- One atomic step touches exactly one file. Files stay <= 400 lines.

## Common error codes
- `FILE_ALREADY_OPEN` — the file is open in another session bundle. STOP and report
  (the orchestrator clears it); do not work around it.
- `VALIDATION_ERROR` — fix via edit -> preview -> commit (see above).
- `USE_CST_COMMANDS` — you used `get_file_lines` on a healthy `.py`; use `universal_file_preview` instead.
- `MODIFIED_NOT_WRITTEN` on close — you have uncommitted edits; commit first, or close to discard.
- `UPSTREAM_UPLOAD_FAILED` — STOP and report; do not retry blindly.
