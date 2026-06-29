#!/usr/bin/env python3
"""Read-only: locate CalibrationObservation occurrences in concept_atomic_matrix
and classify each as telemetry (G-005/T-003) or calibration (G-003). Prints
JSON pointers. Changes nothing."""
import os, yaml
P = "docs/plans/concept_atomic_matrix.yaml"
with open(P, encoding="utf-8") as f:
    d = yaml.safe_load(f)
TARGET = "CalibrationObservation"
TELE = "T-003-telemetry-feedback"

def classify_row(row):
    steps = " ".join(row.get("atomic_steps") or [])
    return "telemetry" if TELE in steps else "calibration"

print("== rows ==")
for i, row in enumerate(d.get("rows") or []):
    objs = row.get("objects") or []
    for j, o in enumerate(objs):
        if o == TARGET:
            print(f"/rows/{i}/objects/{j}  concept={row.get('concept')}  class={classify_row(row)}")

print("== checks ==")
for i, c in enumerate(d.get("checks") or []):
    scope = c.get("scope")
    cls = "telemetry" if scope == "G-005/T-003" else ("calibration" if scope == "G-003/T-003" else scope)
    for key in ("t_objects", "a_objects"):
        for j, o in enumerate(c.get(key) or []):
            if o == TARGET:
                print(f"/checks/{i}/{key}/{j}  scope={scope}  class={cls}")
