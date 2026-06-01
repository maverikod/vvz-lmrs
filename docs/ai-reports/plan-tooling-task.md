# Task: Plan Tooling (Validator, Matrix Generator/Linter, Scaffolder)

Status: proposed task description (not a plan artifact, not HRS).
Scope: tooling to author and verify LMRS development plans under the existing
standards. These tools operate ON plan artifacts; they are not part of the LMRS
product itself. Source standards they must enforce:

- plan_standard_machine.yaml (artifact hierarchy L1-L5, required fields,
  invariants I1-I3, coverage_matrices, cascade, statuses, identifiers, layout)
- hrs_mrs_gs_consistency_verification_standard.yaml (cycle_1, cycle_2)
- tactical_step_creation_standard.yaml (t5-t13, loops)
- atomic_step_creation_standard.yaml (a1-a10, matrices, loops)

Motivation: the atomic layer for plan `lmrs` was authored and verified largely
by hand (edit-session per file) plus a one-off audit script
(tools/plan_audit.py). That manual loop is slow and the set-theoretic checks
(object-axis duplicates, concept coverage, matrix<->AS<->spec consistency) are
exactly what a human reading previews tends to miss. During the final audit the
script caught a real object-name collision (CalibrationObservation in both
lmrs.calibration G-002/T-003 and lmrs.telemetry G-005/T-003) that manual review
had not. The three tools below turn that one-off effort into reusable,
deterministic capability.

The three tools should be implemented as code-analysis-server commands
(per metadatastd.yaml: get_schema + metadata + validate_params, registered as
'custom'), so they are reachable the same way as existing plan/file commands.
All three are read-mostly; only the scaffolder writes plan files, and only via
the universal edit-session path required for YAML.

---

## Tool 1 - Plan Validator (read-only)

Goal: one command that runs the full structural + set-theoretic verification of
a plan and returns a per-check PASS/FAIL report with the exact offending items.
NO semantic judgement (prompt self-sufficiency a4/c7 stays with a model); this
tool covers everything mechanical.

Command (proposed): `plan_validate`
Params:
- project_id (required)
- plan_name (required; e.g. `lmrs`) - resolves docs/plans/<plan_name>/
- scope (optional): full | cycle1 | cycle2 | tactical | atomic | matrices
  (default full)
- fail_fast (optional bool, default false)
- format (optional): report | json (default report)

Inputs read (all under docs/plans/<plan_name>/):
- source_spec.md (binding paragraph labels {xxxx}; non-binding blocks excluded)
- spec.yaml (concepts C-NNN: id/name/definition/properties/source_labels;
  relations: from/to/type)
- gs_concept_matrix.yaml, t_concept_matrix.yaml, object_matrix.yaml,
  concept_atomic_matrix.yaml
- G-*/README.yaml, G-*/T-*/README.yaml, G-*/T-*/atomic_steps/*.yaml

Checks to implement (grouped):

I1 Coverage (plan_standard_machine invariants + cycle_1 c5):
- I1.a union(concepts of all G-steps) == concepts(spec); no empty column in
  gs_concept_matrix; matrix agrees with each GS README concepts list both ways.
- I1.b union(relations implemented across G-steps) == relations(spec).
- I1.c union(source_labels of all G-steps) == binding paragraph labels of
  source_spec (fallback coverage check; parse {xxxx} labels, honor
  <!-- non-binding --> blocks).

Cycle_1 (HRS<->MRS):
- c1 every concept/relation has source_labels that exist in source_spec.
- c3 every relation type is one of the 7 allowed (uses/owns/implements/extends/
  depends_on/produces/consumes); no free-form; direction sanity where derivable.
- c5 gs_concept_matrix completeness (== I1.a materialization).

Cycle_2 (GS triple autonomy), mechanical parts only:
- c5 every concept_id/relation referenced by a GS exists in spec with matching
  from/to/type; no dangling.
- c6 every GS source_label exists and is binding in source_spec.

Tactical (t5-t13), mechanical parts:
- t5 every concept_id in a TS exists in spec.
- t6 every TS concept is in parent GS concepts OR reachable via a relation the
  parent GS legitimately touches (flag the rest). This is the check that caught
  the earlier G-005/T-002 escalation.
- t11 inputs/outputs are structured {name,type,description}.
- t12 ts_concept_matrix: per GS, no empty concept column (every GS concept
  covered by >=1 TS).
- t13 object-axis independence at TS level where derivable from matrices.

Atomic (a1-a10), mechanical parts:
- a1 every concept_id in each AS exists in spec.
- a2 each AS target_file is a single non-empty project-relative path.
- a6 priority unique within (target_file, parent TS).
- a7 every depends_on A-NNN exists within the same parent TS.
- a10 (object-axis materialization) every object in object_matrix realized by
  >=1 AS; every AS object present in object_matrix (excluding dunder/service
  names like __all__).
- step_id unique within parent TS.

Matrix <-> artifact consistency (object/work axis, I2):
- object_matrix: no object realized by more than one (module, TS) pair;
  no object name spans multiple modules; object concepts subset of the union of
  its AS concepts; all object concepts exist in spec.
- concept_atomic_matrix: rows cover every spec concept (no empty column);
  rows reference only existing concepts; for each scope check t_concepts ==
  a_concepts and t_objects == a_objects and result == green.
- README atomic_steps lists match the A-*.yaml files actually present on disk
  (both directions).

Output:
- report mode: one line per check, [PASS]/[FAIL], with offending items listed.
- json mode: {check_id, status, findings:[...]} array + overall green bool.
- exit semantics: overall green only if every check passes.

Notes / lessons to bake in:
- Built-in parser sanity gates (e.g. expected concept count) so a parsing
  regression fails loudly instead of silently passing.
- Classify object/concept occurrences by artifact PATH (which G/T owns them),
  not by T-number alone: T-003 exists under several G-steps; comparing on the
  bare T-number misclassifies (this nearly happened in the manual audit).
- Use PyYAML (canonical) - do not hand-roll a YAML subset parser; the subset
  parser drifted on `- key:` sequences at key indent.

## Tool 2 - Matrix Generator / Linter

Goal: rebuild the four coverage matrices from the authoritative artifacts and
diff against the on-disk matrices, so matrices cannot silently drift from the
GS/TS/AS files. Two modes: lint (diff only, read-only) and regenerate (write).

Command (proposed): `plan_matrices`
Params:
- project_id (required)
- plan_name (required)
- matrix (optional): gs_concept | ts_concept | object | concept_atomic | all
  (default all)
- mode (optional): lint | regenerate (default lint)
- write (optional bool, default false; only honored when mode=regenerate)

Behavior:
- gs_concept_matrix: derive from each G-*/README.yaml concepts list; rows =
  G-steps, columns = spec concepts; mark coverage; flag empty columns and
  GS<->matrix disagreements.
- ts_concept_matrix: per GS, derive from each T-*/README.yaml concepts list;
  flag empty concept columns per GS.
- object_matrix: derive object inventory from AS `objects` fields grouped by
  module/target_file and tactical_steps; flag duplicate ownership across TS,
  cross-module name collisions, and concept mismatches vs AS.
- concept_atomic_matrix: derive concept -> objects -> AS rows and per-scope
  checks from the AS files; flag mismatches against the stored matrix.
- lint mode prints a unified diff (derived vs on-disk) and a PASS/FAIL per
  matrix; writes nothing.
- regenerate mode (write=true) rewrites the matrices via the universal
  edit-session path (YAML must go through universal_file_open create/edit +
  write preview->commit; never create_text_file for YAML). Preserve key order
  and existing formatting conventions as much as the writer allows; show the
  diff before commit.

Constraint: regenerate must respect cascade - it only materializes what the
GS/TS/AS already state; it must never invent coverage. If a derived matrix
would change semantics (not just formatting), it must stop and report rather
than overwrite.

## Tool 3 - Artifact Scaffolder

Goal: create new plan artifacts (G-step, T-step, A-step) from templates with
correct required fields and safe YAML quoting, so authors stop hitting the
manual pitfalls (YAML colon-in-prompt parse errors; missing required fields;
wrong path/slug; duplicate ids).

Command (proposed): `plan_scaffold`
Params:
- project_id (required)
- plan_name (required)
- level (required): global | tactical | atomic
- parent (required for tactical/atomic): G-NNN (for tactical), or G-NNN/T-NNN
  (for atomic)
- step_id (optional; auto-assign next free zero-padded id within parent if
  omitted)
- slug (required): kebab-case directory/name slug
- fields (optional object): initial values for required fields (name,
  description, concepts, target_file, operation, priority, depends_on, etc.)
- dry_run (optional bool, default true): show the file(s) that would be created
  and where, without writing

Behavior:
- Compute the correct path per plan_standard_machine layout:
  - global:  docs/plans/<plan>/G-NNN-<slug>/README.yaml
  - tactical: docs/plans/<plan>/G-NNN-*/T-NNN-<slug>/README.yaml
  - atomic:   docs/plans/<plan>/G-NNN-*/T-NNN-*/atomic_steps/A-NNN-<slug>.yaml
- Emit the required_fields skeleton for that level (per the standards),
  status: draft, empty child lists where applicable.
- Auto-assign the next free id within the parent scope; reject duplicates.
- Safe YAML: any scalar containing ': ', leading '-', quotes, or other YAML
  metacharacters (notably AS `prompt` and `role`) must be emitted as a quoted
  scalar so it never parses as a mapping. This directly prevents the
  parse-error class hit during manual authoring (registry.register(Cls,
  "custom") and 'X: Y' inside prompts).
- Create YAML via the universal edit-session path (open create=true ->
  write preview -> commit -> close); verify by re-reading; never create_text_file
  for YAML.
- After creating an A-step, optionally update the parent TS README atomic_steps
  list (guarded; show diff).
- dry_run=true by default; only writes when explicitly dry_run=false.

---

## Cross-cutting requirements for all three

- Reachable as code-analysis-server commands; documented per metadatastd.yaml
  (get_schema, metadata with usage_examples/error_cases/best_practices,
  validate_params with semantic checks, registered 'custom').
- Read paths use disk as source of truth; writers use the universal
  edit-session workflow for YAML and verify with a separate read after commit.
- Honor forbidden targets (.venv, site-packages) and project-relative paths.
- Deterministic, machine-checkable output (report + json) so they can run in a
  pre-freeze gate.
- Out of scope (stays with a reasoning model): semantic self-sufficiency
  (a4/c7), whether a concept is truly an entity-with-behavior (c4), and any
  source_spec authoring (HRS is human-owned).

## Suggested sequencing

1. Validator first (pure read; immediate value; encodes all the checks).
2. Matrix generator/linter second (reuses the validator's derivation logic).
3. Scaffolder last (writer; benefits from validator as a post-create gate).

## Reference: interim artifacts from this session

- tools/plan_audit.py - one-off read-only audit (I1.a, I2 object-axis,
  a1/a2/a6/a7, matrix/checks consistency). Final run: GREEN, 49 concepts,
  92 AS files, 92 objects, 20 modules. Seed logic for Tool 1.
- tools/plan_locate.py - one-off locator that classified object occurrences by
  artifact path. Seed logic for the path-based classification in Tool 1/2.
These are interim; the real tools above should supersede them.
