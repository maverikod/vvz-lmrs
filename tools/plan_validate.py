"""LMRS development-plan structural validator (``plan_validate``).

PURPOSE
=======
Read-only **mechanical** integrity checker for an LMRS development plan that
follows ``plan_standard_machine.yaml`` (the five-level artifact hierarchy
HRS -> MRS -> Global Step -> Tactical Step -> Atomic Step).

This tool verifies *compositional* / *referential* integrity only -- the
"is everything wired up and present" layer. It is the materialized,
re-runnable form of the set-theoretic checks that cannot be done reliably by
manual preview because earlier tool results get evicted from context
(object-axis duplicate detection, concept coverage across ~50 concepts, etc.).

WHAT THIS TOOL IS NOT
=====================
It is **not** a semantic verifier. It deliberately does NOT attempt any of the
judgement-based checks owned by the standards' verification cycles:

  * cycle_1 c1/c2  -- whether each MRS concept/relation is a faithful tezic
    conspectus of the binding HRS paragraphs it cites (semantic fidelity).
  * cycle_2 c6/c7  -- whether a (HRS + MRS + GS) triple is a self-sufficient
    brief for an executor.
  * tactical t7/t9 -- executor completeness / non-redundancy of a tactical step.
  * atomic a4/a8   -- prompt self-sufficiency and post-prior-AS file-state
    correctness inside an atomic-step prompt.

Those require reading the source artifacts in full under ``zero_trust_reread``
and exercising judgement about meaning. This script must never be presented as
a substitute for them. A GREEN result here means "the plan is structurally
sound and fully wired"; it says nothing about whether the wiring is *correct*
in meaning.

CHECKS PERFORMED
================
MRS (spec.yaml)
  M1  spec.yaml parses as YAML and has the expected top-level shape.
  M2  every concept has the required fields.
  M3  concept_id values are unique and match the C-NNN pattern.
  M4  every relation uses one of the seven allowed relation types.
  M5  every relation endpoint resolves to an existing concept_id.

PLAN TREE (G/T/A README.yaml + atomic_steps/*.yaml)
  P1  every artifact file parses as YAML.
  P2  directory <step_id> prefix agrees with the step_id field.
  P3  step_id uniqueness within the correct scope.
  P4  every concept_id referenced resolves to an existing MRS concept.

MATRICES
  X1  gs_concept_matrix: no empty concept column (I1.a).
  X2  t_concept_matrix: every concept realized by a TS (t12).
  X3  object_matrix: every object realized, no name collision (I2).

INVENTORY
  Counts of G / T / A / concepts / relations / objects against baseline.

OUTPUT
======
Prints a single JSON document to stdout with ok/inventory/findings. Exit code
0 when ok, 1 otherwise. Performs NO writes.

FUTURE
======
Intended to become the MCP command ``plan_validate`` described in
docs/ai-reports/plan-tooling-task.md.

Author: Vasiliy Zdanovskiy / assistant
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required (pip install pyyaml)\n")
    raise

ALLOWED_RELATION_TYPES = {
    "uses",
    "owns",
    "implements",
    "extends",
    "depends_on",
    "produces",
    "consumes",
}

CONCEPT_ID_RE = re.compile(r"^C-\d{3}$")
G_ID_RE = re.compile(r"^G-\d{3}$")
T_ID_RE = re.compile(r"^T-\d{3}$")
A_ID_RE = re.compile(r"^A-\d{3}$")

BASELINE = {"g_steps": 7, "t_steps": 20, "a_steps": 92, "concepts": 54}

PLANS_SUBDIR = Path("docs/plans")


class Findings:
    """Ordered collector of validation findings."""

    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []

    def add(self, check: str, severity: str, where: str, message: str) -> None:
        self.items.append(
            {
                "check": check,
                "severity": severity,
                "where": where,
                "message": message,
            }
        )

    def error(self, check: str, where: str, message: str) -> None:
        self.add(check, "ERROR", where, message)

    def warn(self, check: str, where: str, message: str) -> None:
        self.add(check, "WARN", where, message)

    def info(self, check: str, where: str, message: str) -> None:
        self.add(check, "INFO", where, message)

    def has_errors(self) -> bool:
        return any(i["severity"] == "ERROR" for i in self.items)


def _load_yaml(path: Path, findings: Findings, check: str) -> Optional[Any]:
    """Parse a YAML file, recording a finding on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.error(check, str(path), f"cannot read file: {exc}")
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        findings.error(check, str(path), f"YAML parse error: {exc}")
        return None


def check_mrs(plans_dir: Path, findings: Findings) -> Dict[str, Any]:
    """Run M1-M5. Returns resolved concept_id set and relations."""
    result: Dict[str, Any] = {"concept_ids": set(), "relations": []}
    spec_path = plans_dir / "spec.yaml"
    data = _load_yaml(spec_path, findings, "M1")
    if data is None:
        return result
    if not isinstance(data, dict) or "concepts" not in data or "relations" not in data:
        findings.error("M1", str(spec_path), "missing top-level 'concepts' or 'relations'")
        return result

    concepts = data.get("concepts") or []
    required = ("concept_id", "name", "definition", "properties", "source_labels")
    seen: Dict[str, int] = {}
    for idx, concept in enumerate(concepts):
        where = f"spec.yaml#/concepts/{idx}"
        if not isinstance(concept, dict):
            findings.error("M2", where, "concept entry is not a mapping")
            continue
        for field in required:
            if field not in concept:
                findings.error("M2", where, f"missing required field '{field}'")
        cid = concept.get("concept_id")
        if not isinstance(cid, str) or not CONCEPT_ID_RE.match(cid):
            findings.error("M3", where, f"invalid concept_id: {cid!r}")
            continue
        if cid in seen:
            findings.error("M3", where, f"duplicate concept_id {cid} (first at index {seen[cid]})")
        else:
            seen[cid] = idx
    result["concept_ids"] = set(seen)

    relations = data.get("relations") or []
    for idx, rel in enumerate(relations):
        where = f"spec.yaml#/relations/{idx}"
        if not isinstance(rel, dict):
            findings.error("M4", where, "relation entry is not a mapping")
            continue
        rtype = rel.get("type")
        if rtype not in ALLOWED_RELATION_TYPES:
            findings.error("M4", where, f"relation type not allowed: {rtype!r}")
        for endpoint in ("from_concept", "to_concept"):
            ref = rel.get(endpoint)
            if ref not in result["concept_ids"]:
                findings.error("M5", where, f"{endpoint} {ref!r} not an existing concept_id")
        result["relations"].append(rel)
    return result


def _iter_step_files(plans_dir: Path) -> List[Tuple[str, Path]]:
    """Yield (kind, path) for every G/T/A artifact file."""
    out: List[Tuple[str, Path]] = []
    for g_dir in sorted(plans_dir.glob("G-*")):
        if not g_dir.is_dir():
            continue
        g_readme = g_dir / "README.yaml"
        if g_readme.exists():
            out.append(("G", g_readme))
        for t_dir in sorted(g_dir.glob("T-*")):
            if not t_dir.is_dir():
                continue
            t_readme = t_dir / "README.yaml"
            if t_readme.exists():
                out.append(("T", t_readme))
            a_dir = t_dir / "atomic_steps"
            if a_dir.is_dir():
                for a_file in sorted(a_dir.glob("A-*.yaml")):
                    out.append(("A", a_file))
    return out


def check_plan_tree(plans_dir: Path, concept_ids: set, findings: Findings) -> Dict[str, Any]:
    """Run P1-P4. Returns counts and referenced-concept set."""
    counts = {"G": 0, "T": 0, "A": 0}
    g_ids: Dict[str, str] = {}
    t_ids: Dict[str, set] = {}
    a_ids: Dict[str, set] = {}
    referenced: set = set()

    for kind, path in _iter_step_files(plans_dir):
        data = _load_yaml(path, findings, "P1")
        if data is None:
            continue
        if not isinstance(data, dict):
            findings.error("P1", str(path), "artifact root is not a mapping")
            continue
        counts[kind] += 1
        step_id = data.get("step_id")
        rel = path.relative_to(plans_dir).as_posix()

        if kind == "G":
            if not isinstance(step_id, str) or not G_ID_RE.match(step_id):
                findings.error("P2", rel, f"invalid G step_id: {step_id!r}")
            elif not path.parent.name.startswith(step_id):
                findings.error("P2", rel, f"dir {path.parent.name} != step_id {step_id}")
            if isinstance(step_id, str):
                if step_id in g_ids:
                    findings.error("P3", rel, f"duplicate G step_id {step_id}")
                g_ids[step_id] = rel
        elif kind == "T":
            parent_g = path.parent.parent.name
            if not isinstance(step_id, str) or not T_ID_RE.match(step_id):
                findings.error("P2", rel, f"invalid T step_id: {step_id!r}")
            elif not path.parent.name.startswith(step_id):
                findings.error("P2", rel, f"dir {path.parent.name} != step_id {step_id}")
            if isinstance(step_id, str):
                bucket = t_ids.setdefault(parent_g, set())
                if step_id in bucket:
                    findings.error("P3", rel, f"duplicate T step_id {step_id} in {parent_g}")
                bucket.add(step_id)
        elif kind == "A":
            parent_t = path.parent.parent.name
            if not isinstance(step_id, str) or not A_ID_RE.match(step_id):
                findings.error("P2", rel, f"invalid A step_id: {step_id!r}")
            if isinstance(step_id, str):
                bucket = a_ids.setdefault(parent_t, set())
                if step_id in bucket:
                    findings.error("P3", rel, f"duplicate A step_id {step_id} in {parent_t}")
                bucket.add(step_id)

        for cid in data.get("concepts") or []:
            referenced.add(cid)
            if cid not in concept_ids:
                findings.error("P4", rel, f"concept {cid!r} not in MRS")

    return {"counts": counts, "referenced": referenced}


def check_matrices(plans_dir: Path, concept_ids: set, findings: Findings) -> Dict[str, Any]:
    """Run X1-X3 across the coverage/independence matrices."""
    out: Dict[str, Any] = {"objects": 0}

    gs = _load_yaml(plans_dir / "gs_concept_matrix.yaml", findings, "X1")
    covered = _collect_matrix_concepts(gs)
    if covered is not None:
        for cid in sorted(concept_ids - covered):
            findings.error("X1", "gs_concept_matrix.yaml", f"concept {cid} in no GS")

    tcm = _load_yaml(plans_dir / "t_concept_matrix.yaml", findings, "X2")
    tcm_covered = _collect_matrix_concepts(tcm)
    if tcm_covered is not None:
        for cid in sorted(concept_ids - tcm_covered):
            findings.warn("X2", "t_concept_matrix.yaml", f"concept {cid} realized by no TS")

    obj = _load_yaml(plans_dir / "object_matrix.yaml", findings, "X3")
    objects = _collect_object_names(obj)
    if objects is not None:
        out["objects"] = len(objects)
        for name in sorted(_duplicates(objects)):
            findings.error("X3", "object_matrix.yaml", f"object name collision: {name}")

    return out


def _collect_matrix_concepts(matrix: Any) -> Optional[set]:
    """Extract every C-NNN token appearing in a matrix doc."""
    if matrix is None:
        return None
    found: set = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if CONCEPT_ID_RE.match(node):
                found.add(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and CONCEPT_ID_RE.match(k):
                    found.add(k)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(matrix)
    return found


def _collect_object_names(matrix: Any) -> Optional[List[str]]:
    """Extract object names from object_matrix (may contain dupes)."""
    if matrix is None:
        return None
    names: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("object", "name", "object_name", "qualified_name"):
                val = node.get(key)
                if isinstance(val, str):
                    names.append(val)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(matrix)
    return names


def _duplicates(items: List[str]) -> set:
    seen: set = set()
    dupes: set = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    return dupes


def validate(plans_dir: Path) -> Dict[str, Any]:
    """Run all checks and return the report dict."""
    findings = Findings()

    mrs = check_mrs(plans_dir, findings)
    concept_ids = mrs["concept_ids"]

    tree = check_plan_tree(plans_dir, concept_ids, findings)
    matrices = check_matrices(plans_dir, concept_ids, findings)

    inventory = {
        "g_steps": tree["counts"]["G"],
        "t_steps": tree["counts"]["T"],
        "a_steps": tree["counts"]["A"],
        "concepts": len(concept_ids),
        "relations": len(mrs["relations"]),
        "objects": matrices["objects"],
    }
    for key, expected in BASELINE.items():
        actual = inventory.get(key)
        if actual != expected:
            findings.info("INV", "inventory", f"{key}={actual} differs from baseline {expected}")

    return {
        "ok": not findings.has_errors(),
        "inventory": inventory,
        "findings": findings.items,
    }


def _resolve_plans_dir(argv: List[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    here = Path(__file__).resolve()
    root = here.parent.parent
    return root / PLANS_SUBDIR


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    plans_dir = _resolve_plans_dir(argv)
    if not plans_dir.is_dir():
        report = {
            "ok": False,
            "inventory": {},
            "findings": [
                {
                    "check": "ENV",
                    "severity": "ERROR",
                    "where": str(plans_dir),
                    "message": "plans directory not found",
                }
            ],
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    report = validate(plans_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
