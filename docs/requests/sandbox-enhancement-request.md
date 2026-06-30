# Sandbox Enhancement Request — Standard Toolset, Missing Capabilities, and Interactive Shell

- Author: Vasiliy Zdanovskiy
- email: vasilyvz@gmail.com
- Date: 2026-06-29
- Target: mcp-terminal maintainers
- Context project: LMRS (project_id 03d7a632-fae5-4290-bcee-ba84d19dc1c9)

## 1. Current state (observed)

- `mcp-terminal` provides per-project Docker sandboxes via `terminal_run` (asynchronous, non-interactive) and allowlisted real-host execution via `terminal_host_exec`.
- On the LMRS sandbox: Python 3.12.13 and pip 24.0 are present, but the project `.venv` is bare — only `pip` is installed. No `mypy`, `flake8`, `black`, `isort`, `pytest`, `pylint`, etc. out of the box.
- The QA toolset had to be `pip install`ed per project, which (a) requires `network: package_registry` + a `workspace_write` session, (b) mutates each project's `.venv`, and (c) must be repeated for every project.
- The container root filesystem is read-only, so OS-level tools cannot be added at runtime (no `apt`); only Python packages can be installed into the mounted `/workspace/.venv`.
- `terminal_run` is not a TTY: `command: "bash"` exits immediately. There is no interactive shell at any level.
- `terminal_host_exec` is currently ENABLED (runs as `root`, `execution_target: host_ssh`) but allowlisted-only (sudo/docker hard-blocked, key-guard).
- A WAF (Cloudflare) in front of the proxy rejects complex-but-safe shell strings (pipes, `2>&1`, for-loops, `command -v`) with HTTP 403, forcing single simple commands.

## 2. Request A — bake a standard diagnostic/QA toolset into the default image

So that every project sandbox has these without a per-project install step:

- Lint / format: `ruff`, `flake8`, `black`, `isort`, `pylint`
- Types: `mypy`
- Tests / coverage: `pytest`, `pytest-cov`, `coverage`
- Security / quality: `bandit`, `vulture`, `radon`

Use latest stable versions. Rationale: consistent CI-grade diagnostics, no network/install step on first use, no per-project venv drift.

## 3. Request B — additional commands/utilities needed for normal work

Because the root filesystem is read-only, these must be baked into the image:

- VCS: `git`
- Search / inspect: `ripgrep` (`rg`), `fd`, `tree`, `jq`, `yq`, `less`
- Network / debug: `curl`, `wget`, `ss` (or `netstat`), `ca-certificates`
- Build basics (for packages with native deps): `make`, `gcc` / build-essential, `pkg-config`
- Quick view/edit (optional): `nano`, `vim`
- Process inspection (optional): `ps`, `lsof`, `htop`
- Python helpers (optional): `ipython`, `pip-tools`

## 4. Request C — interactive shell capability (the key missing feature)

There is currently no interactive TTY at any level. We request a persistent, interactive shell session.

Desired behavior:

- Allocate a PTY inside a `keep_container` sandbox container and allow streaming I/O: write to stdin and read incremental stdout/stderr, so an operator/agent can drive REPLs (`python`, `ipython`), debuggers (`pdb`), prompts, `ssh`, `top`, etc.
- A persistent shell whose environment and state survive across multiple inputs within one session.

Proposed API shape (any one is acceptable):

- `terminal_attach(project_id, session_id)` → open/attach a persistent PTY on the session's kept container.
- `terminal_send(project_id, session_id, data)` → write bytes/keystrokes to the PTY stdin.
- `terminal_stream` / `terminal_read(..., follow=true)` → incremental output since last offset.
- `terminal_resize(project_id, session_id, cols, rows)` and `terminal_detach(project_id, session_id)`.
- Alternatively: a WebSocket/stream endpoint bound to a session shell.

Acceptance criteria:

- Can start a `python` REPL, send lines, and observe prompts and output.
- Can run `pdb` and interact with it.
- A long-lived shell keeps `cd`, env vars, and background state across inputs within one session.

Security must be preserved: same sandbox isolation (read-only root, dropped capabilities, no docker/sudo), per-project single-writer policy, and idle/timeout kills.

## 5. Other missing capabilities / friction

- WAF false positives: safe-but-complex shell strings (pipes, redirects, loops) are blocked with HTTP 403. Request: allowlist/relax WAF rules for `terminal_run` command bodies, or route terminal payloads so content-based WAF rules do not apply.
- Sessionless `terminal_host_exec` output ergonomics: results are only readable via the returned `host_run_id`, but `terminal_get_status` / `terminal_tail` reject calls without project/session context (return `INVALID_PROJECT_ID`). Request a documented, working sessionless read path (e.g. accept `host_run_id` consistently) and clearer docs.
- Optional: a pre-warmed/pre-pulled image so the first `terminal_run` is not slowed by image pull/build.
- Optional: a per-project "dev requirements" declaration that the bootstrap installs into the venv automatically (so Request A can be customized per project without manual installs).

## 6. Security considerations

- The interactive shell must preserve all current sandbox guarantees.
- `terminal_host_exec` currently runs as `root`. Recommend review: prefer a least-privilege `target_user` and a tightly reviewed allowlist; keep host execution disabled by default in environments that do not need it.

## 7. Priority

1. Interactive shell (Request C) — highest; it is the main missing capability.
2. Standard QA toolset in the image (Request A).
3. Core OS utilities in the image (Request B): at minimum `git`, `ripgrep`, `jq`, `curl`, `tree`.
4. WAF relaxation for sandbox command bodies (Section 5).
