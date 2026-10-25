"""Frozen, repeatable task evaluation for completion and verification quality."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

REQUIRED_FIELDS = {"task_id", "prompt", "acceptance"}


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    cases = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
            missing = REQUIRED_FIELDS - set(case)
            if missing:
                raise ValueError(f"Line {line_no} missing required fields: {sorted(missing)}")
            cases.append(case)
    ids = [c["task_id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("task_id values must be unique")
    return cases


def evaluate_cases(
    cases: Iterable[Mapping[str, Any]],
    execute: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    report_path: str | Path,
) -> dict[str, Any]:
    frozen = list(cases)
    canonical = "\n".join(json.dumps(c, sort_keys=True, ensure_ascii=False) for c in frozen)
    dataset_hash = hashlib.sha256(canonical.encode()).hexdigest()
    rows = []
    for case in frozen:
        try:
            result = dict(execute(case))
            verified = bool(result.get("verified", False))
            accepted = bool(result.get("accepted", False))
            rows.append(
                {
                    "task_id": case["task_id"],
                    "accepted": accepted,
                    "verified": verified,
                    "status": str(result.get("status", "unknown")),
                    "checks": result.get("checks", []),
                    "error": None,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "task_id": case["task_id"],
                    "accepted": False,
                    "verified": False,
                    "status": "error",
                    "checks": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    count = len(rows)
    report = {
        "schema_version": 1,
        "dataset_sha256": dataset_hash,
        "case_count": count,
        "accepted_rate": sum(r["accepted"] for r in rows) / count if count else 0.0,
        "verified_rate": sum(r["verified"] for r in rows) / count if count else 0.0,
        "accepted_and_verified_rate": sum(r["accepted"] and r["verified"] for r in rows) / count
        if count
        else 0.0,
        "results": rows,
        "notes": "Acceptance and verification are reported separately; acceptance alone is not proof of correctness.",
    }
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)
    return report
