# LMRS session handoff — 2026-07-30

Written at the end of a long session so the next one starts with facts rather
than re-derivation. Everything below was observed, not assumed; where something
is unproven it says so.

Contract in force: prompt bundle `claude-prompts-v1` rev **1.6.9**, active
profile **`local`** (branch `local`, local tools for every script and edit).
Note the 1.6.9 change: `bugfix_acceptance_cycle` now requires reproducing a
defect **on the real deployed server** and running a pipeline check **red
against that same server** before any code change.

---

## 1. Where things stand

**Git.** Branch `local`, pushed to `origin/local`, working tree clean apart from
the prompt-bundle files the user edits by hand (CLAUDE.md, claude/*) which are
intentionally left uncommitted.

**Plan.** lmrs `8b9b0466-b6f0-4790-b003-0c6cc1cd0a3b`, frozen at revision
`36282da8`, mechanical gate green 28/28. All planned steps that had code work
are executed and recorded as succeeded execution attempts.

**Repo verification.** `pipeline tests` 83 passed; `ruff`, `flake8`, `mypy`
green. An argument-free `pipeline` run is red on a workstation by design,
because it includes `commands-live` which requires a deployed server (the
frozen step says skipping is a failure). Name the checks for a repo-only run.

**Deployment.** Host `vvz` = 192.168.254.26, RTX 3090 24 GB. `lmrs-container
0.1.4-1` installed, container on `vasilyvz/lmrs:0.1.4`, port 8012 listening,
adapter serving. Image pushed to Docker Hub. Client published:
`lmrs-client 0.1.4` on PyPI. Previous image `0.1.3` still on the host, so
rollback is one `docker run` away.

**The deployment does not actually serve models.** vLLM never reaches its API
port; see §3. Nine of eighteen commands fail against it; most of those are
stubs, see §4.

Image runtime pins live in `docker/lmrs/pins.env` (in version control):
`vllm/vllm-openai:v0.25.1` + `lmcache==0.5.2`. Pairing rule recorded there:
latest stable LMCache, then the newest vLLM that predates it.

---

## 2. Commits made this session

| Commit | What |
|---|---|
| `19eeaae` | pipeline package: registry, checks, CLI, console script, dev extras, contract tests (plan G-018) |
| `c22c814` | ruff and flake8 reconciled into one setting pair; flushed per-check verdict lines |
| `8e9fc1a` | LMCache operations: `get_lmcache_status`, `purge_lmcache`, two thin commands, 12 tests (G-015) |
| `fb7563b` | one canonical startup seam `start_adapter_server`; both callers delegate (G-009/T-002) |
| `0e4cd33` | startup autoload + `switch_model` + `MODEL_SWITCHING` + queued switch command (G-017) |
| `09b04b4` | the seven missing public commands; surface pinned against the CommandName catalog |
| `9df7ca6` | LMRS client library, PyPI packaging, client/server surface sync test (G-011) |
| `5738594` | Dockerfile pins required and guarded, three volumes; single-run `build.sh`; debian/control (G-012) |
| `9bfa78e` | vLLM/LMCache pins chosen from live PyPI and Docker Hub APIs |
| `533ab46` | proxy acceptance script + `commands-live` named check (G-016) — closed bug c0ca214d |
| `78fe271` | live check now sees structured failures; `estimate` no longer breaks on adapter kwargs |
| `82ccdb1` | forward HF_* into the container; LMCache gate `-r` → `-f` |
| `cfda28e` | reverted `HF_HUB_OFFLINE=1` as a default — the hypothesis did not hold |

Plan repair under cascade `60d1d218`: authored `G-006/T-004` (three atomics) for
five commands that the declared surface named but no step ever registered, and
corrected drifted names in the client step. Bugs `a33e2d22` and `0363018f`
closed with verified fixes.

---

## 3. vLLM does not serve — located, not solved

**Symptom.** Container starts, adapter serves on 8012, but vLLM's own API on
127.0.0.1:8000 never accepts a connection. No log line after
`Using FlashAttention version 2`. EngineCore alive, `futex_do_wait`, ~11 % CPU,
~15 GB VRAM held.

**Located by stack dump, not by guessing:**

```
docker exec -u root lmrs pip install py-spy
docker exec -u root --privileged lmrs py-spy dump --pid <EngineCore pid>
```

shows it blocked in `huggingface_hub snapshot_download` ←
`vllm/model_executor/model_loader/default_loader.py:_prepare_weights`. vLLM
calls the Hub on **every** start even with weights cached, and unauthenticated
that call stalls silently.

**Three hypotheses tested and DISPROVEN. Do not spend time on them again.**

1. *LMCache/vLLM version incompatibility.* Disabled the KV connector entirely
   (`LMCACHE_CONFIG_FILE=/nonexistent`) and restarted: vLLM still never served
   in 8 minutes of 30-second polls. The `lmcache 0.5.2` pin is not the cause.
2. *A download in progress, merely slow.* No `.incomplete` files, zero byte
   growth over 45 s, all four shards present since 7 July. Note the trap: the
   process **is** inside `snapshot_download`, it just never transfers, so
   "no traffic" does not mean "no attempt". This is the reasoning error that
   sent the first diagnosis the wrong way.
3. *`HF_HUB_OFFLINE=1` serves from cache instead.* It does not. vLLM then fails
   fast with `LocalEntryNotFoundError: Cannot find an appropriate cached
   snapshot folder for the specified revision`, **even though the cache is
   consistent**: `refs/main` = `c03e6d358207e414f1eca0bb1891e29f1db0e242`, the
   snapshot directory carries that same hash, all four shards plus
   `model.safetensors.index.json` are present, no broken symlinks.

**Next lead, untested.** Find which `allow_patterns` and `revision` vLLM passes
to `snapshot_download`, then call it by hand inside the container with the same
arguments. If it asks for a file the snapshot lacks (a chat template, a
preprocessor config), that single fact explains both observations: online it
leaves to fetch the missing file and stalls, offline it honestly reports the
snapshot as unsuitable. Also unknown and worth settling early: **whether 0.1.3
ever served** — the baseline was never captured, so it is not established that
the v0.25.1 pin introduced this.

Host was restored to its intended configuration after every experiment. Backups
left in place: `/root/lmcache.yaml.abtest-backup`,
`/root/lmrs.default.abtest-backup`, `/root/lmrs.default.pre-offline`,
`/root/lmrs-container.pre-offline`.

---

## 4. The live check was lying, and what it exposed

`commands-live` first reported **18/18 green** against the deployed server while
`chat` was answering "vLLM unavailable". Cause: the framework client never
raises on failure. A failed command returns `result.success = false` with an
`error`; a command with a negative domain outcome returns
`payload.success = false` with a stable `reason_code`; a queued command wraps
either inside a job envelope. The check caught only exceptions.

Fixed in `78fe271` — it now inspects all three layers. Run against the same
server it is honestly **red, 9 of 18**. That run immediately exposed a second
defect: `EstimateCommand` splatted every incoming parameter into the canonical
handler, and the adapter injects its own (`context`), so `estimate` failed with
`_estimate_and_admit() got an unexpected keyword argument 'context'`. Also
fixed there.

**Bigger finding: much of the domain layer is stubs, not a deployment problem.**
`DiskModelCache.preload` unconditionally returns `PRELOAD_EXECUTOR_UNAVAILABLE`;
docstrings say "contract stubs"; `RuntimeClient` carries
`RUNTIME_EXECUTOR_UNAVAILABLE`. That is why the live run shows
`MODEL_NOT_CACHED`, `MODEL_NOT_SERVED_BY_VLLM`, `PRELOAD_EXECUTOR_UNAVAILABLE`.
The user has asked for these to be implemented ("приступай к дописыванию") —
that is plan execution work, not a bug fix.

---

## 5. Open work, in the order it should be taken

1. **vLLM root cause** (§3, next lead). Without a serving engine nothing about
   model operations can be verified live.
2. **Release 0.1.5.** Version is still 0.1.4, so the deployed image carries the
   `estimate` defect and the old run script. Needs the bump, `build.sh`
   (~1 h: 28 GB image build, push, host pull), deploy, then `commands-live`
   against the live server. The bugfix cycle mandates the bump before deploy.
3. **Stale advertised version.** `/etc/lmrs/config.json` is a conffile that
   survives upgrades and hard-codes `registration.metadata.version = 0.1.3`,
   so a 0.1.4 deployment advertises 0.1.3 to the proxy. **User agreed** to take
   the version from the installed package and drop the field from the config.
   Not implemented. `create_and_run_server` accepts a `version=` argument;
   `lmrs.adapter.info` already resolves the real version via
   `importlib.metadata`.
4. **Implement the stub domain layer** (§4). Large; start from
   `DiskModelCache.preload` and `RuntimeClient`.

---

## 6. Recipes worth keeping

**Run the live check.** Certificates come from the host
(`/etc/lmrs/certs/{ca.crt,lmrs-client.crt,lmrs-client.key}`):

```
LMRS_LIVE_HOST=192.168.254.26 LMRS_LIVE_PORT=8012 LMRS_LIVE_PROTOCOL=https \
LMRS_LIVE_CA=<ca> LMRS_LIVE_CERT=<crt> LMRS_LIVE_KEY=<key> \
.venv/bin/pipeline commands-live
```

**Long remote work must be detached on the host.** A 28 GB `docker pull` under
`ssh` outlives the client timeout and gets SIGTERMed (seen as exit 143). Start
it with `nohup … &` on the host and poll for the image, otherwise a half-done
deploy is possible. In this session `set -e` saved it: the pull died before
`dpkg` ran and 0.1.3 kept serving.

**Adding a public command touches three files**, not one: `CommandName` in
`lmrs/commands.py`, the class in `lmrs/adapter/registration.py`, and
`AdapterExposure.command_surface` in `lmrs/contracts.py` — an existing test
asserts the surface equals the registered classes, in the same order.

**`pipeline tests` needs the `[server]` extra** installed; the adapter tests
import `mcp_proxy_adapter`.

**Plan mutation.** `cascade_begin` refuses a fully frozen plan — call
`plan_unfreeze` first, it opens the cascade itself. `step_create` only
scaffolds; fill fields with `step_update`. Every create bumps the revision and
stales the parent context block, so recompile before each one, and prefer
`block_rebuild` (summary-only) over `context_common` (≈9 k tokens per call).
A new tactical step needs concepts ⊆ its parent's, and a
`step_dependency_add` edge to any sibling writing the same file.

**Freezing** must use `scope=whole_plan`; a branch-scoped freeze crashes
(planmgr bug 36414056). Freezing makes a new revision, which stales context
blocks again — rebuild once more afterwards or the gate reports them.

---

## 7a. Update, later the same day: the domain layer is implemented

Commit `7064cc2` on `local` closes §5 item 4. The disk cache is backed by the
hub cache directory the runtime downloads into; `RuntimeClient` executes against
vLLM and normalizes the answer; VRAM is measured through `nvidia-smi` with the
service baseline persisted in `/var/lmrs/vram-facts.json`; KV cost is derived
from the cached model's own `config.json`; and `chat` is admitted before it
reaches the runtime instead of calling vLLM directly. `pipeline tests` is 150
passed, ruff/flake8/mypy green.

Three things the next session must know.

**Nothing of this is verified live.** The runtime paths need a serving vLLM, so
§3 is still item one. `commands-live` also now requires `LMRS_LIVE_MODEL` to name
the served model, and drives the disk-cache commands against a scratch model
(`LMRS_LIVE_CACHE_MODEL`, default `hf-internal-testing/tiny-random-gpt2`) - the
cache commands delete real weights now, and the old profile would have deleted
the deployed model.

**Two response shapes changed.** `chat` returns the normalized command result
(outcome, reason code, token breakdown, capacity snapshot, payload) instead of a
raw vLLM completion, and `capacity` reports null where it has no measurement
rather than a zero. `local_model_cache_preload` is queued on both client and
server.

**New operator settings** are documented in `packaging/lmrs.default.template`
and forwarded by `packaging/bin/lmrs-container`: resident services, safety
margin, runtime reserve, per-request and batch overheads, KV dtype, queue TTL,
hardware profile id, VRAM facts path, cache root. The entrypoint now derives
`VLLM_BASE_URL` from `VLLM_HOST`/`VLLM_PORT` and sets `LMRS_LMCACHE_ENABLED`
from the same condition that enables the KV connector.

## 7. Decisions the user has already made

- Push the working branch without asking; do not wait for confirmation.
- Version pins: newest stable **and compatible**, chosen by evidence.
- Advertised version must come from the installed package, not the conffile.
- Publish the client to PyPI (0.1.4 done).
- How to fix the live-check defect was left to the agent's judgement.
