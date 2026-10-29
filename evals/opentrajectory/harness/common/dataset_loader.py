from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATASET_FILES = {
    "core": "core.jsonl",
    "cross-domain": "cross-domain.jsonl",
    "boundary": "boundary.jsonl",
    "regression": "regression.jsonl",
}


@dataclass(frozen=True)
class DatasetCase:
    case_id: str
    dataset_split: str
    record: dict[str, Any]


@dataclass(frozen=True)
class LoadedDataset:
    dataset_id: str
    dataset_version: str
    dataset_dir: Path
    dataset_files: dict[str, str]
    dataset_hashes: dict[str, str]
    cases: list[DatasetCase]
    split_counts: dict[str, int]


class DatasetLoaderError(RuntimeError):
    pass


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], str]:
    if not path.exists():
        raise DatasetLoaderError(f"Missing dataset file: {path}")
    raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetLoaderError(f"Invalid JSON in {path.name} line {line_no}: {exc}") from exc
        if not isinstance(obj, dict):
            raise DatasetLoaderError(f"Expected JSON object in {path.name} line {line_no}")
        rows.append(obj)
    return rows, _sha256_text(raw)


def _extract_dataset_meta(dataset_dir: Path) -> tuple[str, str, dict[str, Any]]:
    manifest_path = dataset_dir / "dataset-manifest.md"
    if not manifest_path.exists():
        raise DatasetLoaderError(f"Missing dataset manifest: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    dataset_id = "strategy-phase1-3"
    dataset_version = "unknown"
    if "Dataset ID | `strategy-phase1-3`" not in text:
        raise DatasetLoaderError("Dataset manifest does not match expected dataset id")
    for line in text.splitlines():
        if line.startswith("| Dataset Version |"):
            dataset_version = line.split("|")[2].strip().strip("`")
    hashes: dict[str, Any] = {}
    capture = False
    for line in text.splitlines():
        if line.startswith("| `core.jsonl` |"):
            hashes["core"] = line.split("|")[4].strip().strip("`")
        elif line.startswith("| `cross-domain.jsonl` |"):
            hashes["cross-domain"] = line.split("|")[4].strip().strip("`")
        elif line.startswith("| `boundary.jsonl` |"):
            hashes["boundary"] = line.split("|")[4].strip().strip("`")
        elif line.startswith("| `regression.jsonl` |"):
            hashes["regression"] = line.split("|")[4].strip().strip("`")
    return dataset_id, dataset_version, hashes


def load_strategy_phase1_3_dataset(dataset_dir: Path) -> LoadedDataset:
    dataset_id, dataset_version, manifest_hashes = _extract_dataset_meta(dataset_dir)

    cases: list[DatasetCase] = []
    split_counts: dict[str, int] = {}
    seen_ids: set[str] = set()
    actual_hashes: dict[str, str] = {}

    for split, filename in DATASET_FILES.items():
        rows, sha = _read_jsonl(dataset_dir / filename)
        actual_hashes[split] = sha
        split_counts[split] = len(rows)
        expected_hash = manifest_hashes.get(split)
        if expected_hash and expected_hash != sha:
            raise DatasetLoaderError(f"Hash mismatch for {filename}: expected {expected_hash}, got {sha}")
        for row in rows:
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise DatasetLoaderError(f"Missing or invalid case_id in {filename}")
            if case_id in seen_ids:
                raise DatasetLoaderError(f"Duplicate case_id detected: {case_id}")
            seen_ids.add(case_id)
            if split != "regression" and row.get("expected") is None:
                raise DatasetLoaderError(f"Missing expected in case {case_id}")
            if split == "regression":
                if row.get("record_type") != "non_executable_regression_placeholder":
                    raise DatasetLoaderError(f"Invalid regression placeholder type in case {case_id}")
            dataset_type = row.get("dataset_type") or row.get("split") or split
            if dataset_type != split:
                # tolerate alternate field names but keep source split authoritative
                pass
            if row.get("execution_policy") is None:
                raise DatasetLoaderError(f"Missing execution_policy in case {case_id}")
            cases.append(DatasetCase(case_id=case_id, dataset_split=split, record=row))

    if len(cases) == 0:
        raise DatasetLoaderError("No dataset cases loaded")

    return LoadedDataset(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_dir=dataset_dir,
        dataset_files=DATASET_FILES.copy(),
        dataset_hashes=actual_hashes,
        cases=cases,
        split_counts=split_counts,
    )
