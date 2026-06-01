#!/usr/bin/env python3
"""Read-only LMRS plan audit. Prints PASS/FAIL per invariant. Changes nothing."""
import os, sys, glob
from collections import defaultdict
import yaml

PLANS = "docs/plans"

findings = []
def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if not ok and detail else ""))
    if not ok:
        findings.append(name)

def load(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)

spec = load(os.path.join(PLANS, "spec.yaml"))
cam = load(os.path.join(PLANS, "concept_atomic_matrix.yaml"))
om = load(os.path.join(PLANS, "object_matrix.yaml"))

spec_concepts = {c["concept_id"] for c in (spec.get("concepts") or []) if isinstance(c, dict) and c.get("concept_id")}
print(f"INFO spec concepts: {len(spec_concepts)}")
if len(spec_concepts) != 49:
    print(f"WARN expected 49 concepts, got {len(spec_concepts)}")

as_files = sorted(glob.glob(os.path.join(PLANS, "G-*", "T-*", "atomic_steps", "*.yaml")))
print(f"INFO atomic step files: {len(as_files)}")
as_by_path = {p: load(p) for p in as_files}

def ts_key(p):
    parts = p.split(os.sep)
    g = next((x for x in parts if x.startswith("G-")), "?")
    t = next((x for x in parts if x.startswith("T-")), "?")
    return g + "/" + t

# 1. I1.a concept coverage
cam_rows = cam.get("rows") or []
cam_row_concepts = {r.get("concept") for r in cam_rows if isinstance(r, dict) and r.get("concept")}
missing = sorted(spec_concepts - cam_row_concepts)
check("I1.a every spec concept appears in concept_atomic_matrix", not missing, f"missing {missing}")
check("concept_atomic_matrix references only existing concepts", not (cam_row_concepts - spec_concepts), f"unknown {sorted(cam_row_concepts - spec_concepts)}")

# 2/3 object_matrix
obj_owner_ts, obj_concepts, obj_modules = {}, {}, {}
for m in (om.get("modules") or []):
    mod = m.get("module", "?")
    for o in (m.get("objects") or []):
        if not isinstance(o, dict) or not o.get("name"):
            continue
        nm = o["name"]
        obj_modules.setdefault(nm, set()).add(mod)
        for ts in (o.get("tactical_steps") or []):
            obj_owner_ts.setdefault(nm, set()).add((mod, ts))
        obj_concepts.setdefault(nm, set()).update(o.get("concepts") or [])
print(f"INFO object_matrix objects: {len(obj_owner_ts)}; modules: {len(om.get('modules') or [])}")
check("object_matrix concepts all exist in spec", not ({c for cs in obj_concepts.values() for c in cs} - spec_concepts), f"unknown {sorted({c for cs in obj_concepts.values() for c in cs} - spec_concepts)}")

dup_owner = {nm: o for nm, o in obj_owner_ts.items() if len({(mm, tt) for mm, tt in o}) > 1}
check("I2 no object realized by more than one (module,TS)", not dup_owner, "; ".join(f"{nm}:{sorted(o)}" for nm, o in dup_owner.items()))
dup_mod = {nm: mm for nm, mm in obj_modules.items() if len(mm) > 1}
check("no object name spans multiple modules", not dup_mod, "; ".join(f"{nm}:{sorted(m)}" for nm, m in dup_mod.items()))

# 4 each object in >=1 AS
as_objects, as_concepts_by_obj = set(), {}
for p, d in as_by_path.items():
    for nm in (d.get("objects") or []):
        if isinstance(nm, str) and nm.startswith("__") and nm.endswith("__"):
            continue  # module export lists etc. are not domain objects
        as_objects.add(nm)
        as_concepts_by_obj.setdefault(nm, set()).update(d.get("concepts") or [])
check("I2 every object_matrix object realized by >=1 AS", not (set(obj_owner_ts) - as_objects), f"orphans {sorted(set(obj_owner_ts) - as_objects)}")
check("every AS object present in object_matrix", not (as_objects - set(obj_owner_ts)), f"AS-only {sorted(as_objects - set(obj_owner_ts))}")

# 5 object concepts subset of AS concepts
subset_viol = [f"{nm}: om{sorted(cs)} !subset AS{sorted(as_concepts_by_obj.get(nm, set()))}" for nm, cs in obj_concepts.items() if not cs.issubset(as_concepts_by_obj.get(nm, set()))]
check("object_matrix concepts subset of AS concepts per object", not subset_viol, "; ".join(subset_viol))

# 6 per-AS structural
bad_concept_refs, multi_target = [], []
for p, d in as_by_path.items():
    for c in (d.get("concepts") or []):
        if c not in spec_concepts:
            bad_concept_refs.append(f"{os.path.basename(p)}:{c}")
    tf = d.get("target_file")
    if not isinstance(tf, str) or not tf:
        multi_target.append(os.path.basename(p))
check("a1 AS concepts all exist in spec", not bad_concept_refs, "; ".join(bad_concept_refs))
check("a2 AS target_file single non-empty", not multi_target, "; ".join(multi_target))

by_ts = defaultdict(list)
for p, d in as_by_path.items():
    by_ts[ts_key(p)].append((p, d))
prio_viol, dep_viol, dup_step = [], [], []
for ts, items in by_ts.items():
    stepids, prio_by_file = set(), defaultdict(list)
    for p, d in items:
        sid = d.get("step_id")
        if sid in stepids:
            dup_step.append(f"{ts}:{sid}")
        stepids.add(sid)
        prio_by_file[d.get("target_file")].append(d.get("priority"))
    for f, prios in prio_by_file.items():
        if len(prios) != len(set(prios)):
            prio_viol.append(f"{ts} {f}: {prios}")
    for p, d in items:
        for dep in (d.get("depends_on") or []):
            if dep not in stepids:
                dep_viol.append(f"{ts}:{d.get('step_id')}->{dep}")
check("a6 priority unique within target_file per TS", not prio_viol, "; ".join(prio_viol))
check("a7 depends_on refers to existing step in TS", not dep_viol, "; ".join(dep_viol))
check("step_id unique within TS", not dup_step, "; ".join(dup_step))

# 7 checks consistency
chk_viol = []
for c in (cam.get("checks") or []):
    if not isinstance(c, dict):
        continue
    if c.get("result") != "green":
        chk_viol.append(f"{c.get('scope')}:result={c.get('result')}")
    if c.get("t_concepts") is not None and c.get("a_concepts") is not None and set(c["t_concepts"]) != set(c["a_concepts"]):
        chk_viol.append(f"{c.get('scope')}:t!=a concepts")
    if c.get("t_objects") is not None and c.get("a_objects") is not None and set(c["t_objects"]) != set(c["a_objects"]):
        chk_viol.append(f"{c.get('scope')}:t!=a objects")
check("concept_atomic_matrix checks green and t==a", not chk_viol, "; ".join(chk_viol))

print()
print("AUDIT RESULT:", "GREEN (no findings)" if not findings else f"{len(findings)} FINDING(S): {findings}")
sys.exit(0 if not findings else 1)
