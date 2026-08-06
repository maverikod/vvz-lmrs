# Context Control: closing a plan step's namespace before a model sees it

- Author: Vasiliy Zdanovskiy
- email: vasilyvz@gmail.com
- Date: 2026-08-06
- Status: design note, derived from measurements taken on this project's own plan steps
- Related contract clauses: `claude/roles/laws.yaml::zero_trust_step_acceptance`,
  `claude/ops/planning-authoring.yaml::declared_name_and_concept_manifests`

## 1. The problem, stated precisely

An executor model given an atomic plan step can fail in ways that survive every
cheap check. It compiles, it lints, it uses only plausible names, and it is
wrong. The failures we measured were not carelessness in the model: each one was
a hole the plan artifact permitted, and each one is closable before any model is
involved.

The naive defence — instruct the executor to escalate when a fact is missing —
does not work. We measured it directly (§2). The reliable defence is to make the
situation unreachable: a step whose context cannot answer it is never dispatched.

## 2. What was measured

Real A-step prompts from `docs/plans/execution_contexts/`, run against the
deployed LMRS with a local 7B executor (`Qwen2.5-Coder-7B-Instruct-AWQ`),
temperature 0.

| Step | Defect in the supplied context | Result |
|---|---|---|
| `A-003-calculate-static-vram` | prompt states the file "contains `VramRuntimeFacts` and `DynamicVramState`" but supplies neither definition | invented both as `NamedTuple`, one an empty `pass` stub; the target function itself was correct |
| `A-003-kv-bytes-per-token` with the C-044 excerpt truncated | the formula line removed from the concept | produced `(layers * kv_heads * head_dim * elem) / declared_context_window`, return type `float` instead of `int` — compiles, lints clean, uses only legal names |
| two control steps with complete context | none | correct; no false escalations |

Escalations: **zero out of two** opportunities, although `no_guessing: true` and
the escalation rule were in the executor's instructions verbatim, and an explicit
one-line escalation channel was offered.

The same steps, rebuilt as manifest-shaped prompts with complete concepts (§4),
produced: the target function alone with nothing invented; the formula verbatim
correct with the declared signature; and, on a definition deliberately withheld,
`ESCALATE: The definition of DynamicVramState is required to implement
calculate_max_dynamic_pool.`

**The variable was the artifact, not the model.**

## 3. Why the namespace is closed

A step's output can reference names from exactly three sources:

1. **names the plan creates** — the union of every step's declared creations;
2. **names already in the codebase** — enumerable authoritatively from CAS
   (`list_code_entities`, `file_structure`, `get_class_hierarchy`);
3. **names from outside the project** — imports.

The first two are already knowable. The third looks open-ended but is not: once
a step declares which modules it imports, the *members* of those modules are
derivable mechanically. You do not enumerate them by hand — you ask Python.

Measured on this repository: importing two declared modules yields 38 symbols
with their real signatures, including members that appear in no line of source
because `@dataclass` synthesised them:

```
VramRuntimeFacts(resident_services: 'tuple[str, ...]',
                 service_baseline_free_vram_bytes: 'int',
                 model_loaded_free_vram_bytes: 'int | None' = None, ...)
```

So the total namespace available to a step is a finite, enumerable set,
computable before the model is engaged.

### 3.1 What does not threaten the closure

Decorators were an early suspicion and are largely a false alarm. `@foo def bar`
does not add a name; it rebinds one, and the binding is statically known — which
is why type checkers handle decorators fine. The real exposure is at the
*attribute* level (`@dataclass` synthesising `__init__`), and §3 shows that
introspection recovers exactly those.

What genuinely mutates a namespace at runtime is narrower: `globals()[name] = …`,
`exec`/`eval`, module-level `__getattr__` (PEP 562), star-imports, `type()` and
metaclass injection, monkeypatching.

An audit of `lmrs/` found **none** of these. The only matches for the dynamic
patterns were six occurrences of `importlib.metadata.version("lmrs")` — reading a
package version, not creating names. Decorators in use: `@dataclass` ×50,
`@staticmethod` ×2, `@classmethod` ×2, all statically transparent.

For this codebase the namespace is therefore already closed, and an escape hatch
for dynamic steps is a formality rather than a routine need. Keep the hatch —
declared explicitly per step, never implicit — so the rule does not have to be
weakened wholesale the first time something legitimate needs it.

## 4. The mechanism

### 4.1 Manifests declared at authoring time

Each atomic step declares its names split by **how each is resolved**, because
the resolution method is what the checker runs:

| Set | Meaning | Resolved by |
|---|---|---|
| `creates` | entities this step introduces; the only new definitions its output may contain | the output's AST |
| `from_context` | entities referenced but not defined here | a real definition present in the compiled bundle |
| `imports` | modules from outside the target file | importing them and introspecting members |

Merging the last two defeats the check: an invented API passes as "it was
imported". They are verified by different mechanisms and must stay separate.

The manifest is not an exhaustive hand-written symbol list. It is a **selector**:
it says which modules and entities this step touches, so the tooling knows which
slice of the derived namespace to inject into the prompt and which names to
permit in the output.

Alongside the names, each declared concept must be embedded **complete, with
every property**. A truncated concept is a missing fact that no name check can
see — removing one line (`key_value_factor: 2`) from C-044 was sufficient to
produce a wrong formula that satisfied every structural rule.

### 4.2 Compilation is a gate

The context compiler must refuse to emit a bundle, with a named reason, when a
`from_context` name has no definition in the bundle, an `imports` module is not
among the project's dependencies, a declared concept is embedded without its full
property set, or a name appears in more than one set.

This is the highest-value element. It converts "the executor should have noticed
and escalated" — measured as unreliable — into "the step never reached the
executor".

### 4.3 Acceptance rules on the result

Four rules, all mechanical, all derived from the manifest:

1. every name in `creates` is present in the output;
2. nothing is defined beyond `creates`;
3. every referenced name resolves in the derived namespace, and **every call is
   compatible with the real signature**;
4. the produced signature matches the declared one.

Rule 3 is not string matching. `inspect.Signature.bind` validates a call site
against the actual callable, before the code runs. Demonstrated on this project:

```
model output from the manifest run     -> clean
hallucinated API (now_epoch)           -> unknown name 'now_epoch' (line 3)
wrong fields on a dataclass constructor-> call VramRuntimeFacts(...) rejected by
                                          the real signature: missing a required
                                          argument: 'resident_services'
```

The namespace is derived from the **installed** versions, so it reflects reality
rather than the model's recollection of a library. That is the durable cure for
hallucinated APIs.

### 4.4 Fragment output contract

The executor returns only the entities in `creates` — not the whole file. CAS
supplies exact boundaries (`file_structure` gives `start_line`/`end_line` per
entity) so the editor splices the fragment back without the model ever seeing the
file.

Effects measured: output fell from 199 tokens (with fabrications) to 135, and one
step to 38 tokens; there is structurally nowhere to define anything outside
`creates`; and output size stops scaling with file size, which is what made a
long step fail on a runtime timeout before.

## 5. What this does not cover

The four rules are necessary and **not sufficient**. A result can satisfy all of
them and still be wrong, because legal names with correct signatures can carry
wrong arithmetic — precisely what the truncated-concept case produced.
`Signature.bind` validates the shape of a call, never its meaning.

That residue belongs to the step's own `verification`, which must therefore be
executable rather than prose. Today it reads:

```yaml
verification:
  type: static_analysis
  target: lmrs/configuration.py::KVCacheProfile.kv_bytes_per_token
  expected: "KVCacheProfile exposes kv_bytes_per_token using layers * 2 * kv_heads * head_dim * kv_element_bytes."
```

A human reads that; a gate cannot. While `expected` is prose, the last and most
expensive class of defect is caught by reviewer attention rather than machinery.

## 6. Operational note

Deriving the namespace **imports the declared modules**, which executes their
top-level code. For ordinary libraries this is harmless, but the right place to
run it is the project's own venv or container, not the planner's process. CAS
already provides that surface (`run_project_module`, `project_pip_list`,
`project_pip_show`).

## 7. Consequences

**Decomposition quality becomes partly mechanical.** "Exhaustive,
non-overlapping" was previously a semantic property caught late, after dependent
steps had already been authored against a faulty decomposition. With a closed
namespace, non-overlap is "no name created twice" and completeness is "no
dangling reference", both decidable at authoring time. Step ordering is likewise
derivable: if B references what A creates, B follows A — so hand-written
`depends_on` becomes checkable against the symbol graph rather than trusted.

**Staleness becomes computable.** Editing a concept's properties invalidates
exactly the steps that declare that concept. The known problem of context blocks
going stale after truth edits turns from "remember to recompile" into a query.

**Small local executors become usable.** A 7B model on one consumer card is an
accurate applicator when the context is complete and the scope is fenced — 2/2
clean on well-formed steps here, including a 915-token output. It is an
unreliable detector of its own missing context. Moving that detection into
authoring is what makes the small model sufficient, and that in turn decides
whether a rented GPU tier is needed at all.

## 8. Honest limits of this note

Sample sizes are small: one run per probe at temperature 0, on steps chosen
because they had already failed. This establishes that the reformulated artifact
repairs known failures; it is not an independent estimate of failure rates. The
per-model conclusions apply to `Qwen2.5-Coder-7B-Instruct-AWQ` and were not
re-measured on other tiers.
