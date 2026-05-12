from __future__ import annotations

import csv
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import DatasetPayload, RunConfig, RunResult, RunStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_runs_dir() -> Path:
    base = os.getenv("RUNS_DIR")
    if base:
        return Path(base)
    return Path(__file__).resolve().parents[1] / "runs"


def get_run_dir(run_id: str) -> Path:
    return get_runs_dir() / run_id


def create_run(dataset: DatasetPayload, config: RunConfig) -> tuple[str, Path]:
    runs_dir = get_runs_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "input.json", dataset.model_dump(mode="json"))
    write_json(run_dir / "config.json", config.model_dump(mode="json"))
    status = RunStatus(
        run_id=run_id,
        state="queued",
        dataset_id=dataset.dataset_id,
        total=min(len(dataset.records), config.limit or len(dataset.records)),
        message="Run queued",
    )
    write_status(run_id, status)
    append_log(run_id, f"Created run for dataset {dataset.dataset_id}")
    return run_id, run_dir


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_log(run_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}\n"
    log_path = get_run_dir(run_id) / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def read_log(run_id: str) -> str:
    return (get_run_dir(run_id) / "run.log").read_text(encoding="utf-8") if (get_run_dir(run_id) / "run.log").exists() else ""


def write_status(run_id: str, status: RunStatus) -> None:
    write_json(get_run_dir(run_id) / "status.json", status.model_dump(mode="json"))


def read_status(run_id: str) -> RunStatus:
    payload = read_json(get_run_dir(run_id) / "status.json")
    return RunStatus.model_validate(payload)


def append_result(run_id: str, result: RunResult) -> None:
    run_dir = get_run_dir(run_id)
    with (run_dir / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(result.model_dump_json() + "\n")


def read_results(run_id: str) -> list[dict[str, Any]]:
    jsonl_path = get_run_dir(run_id) / "results.jsonl"
    if jsonl_path.exists():
        rows = []
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    json_path = get_run_dir(run_id) / "results.json"
    if json_path.exists():
        return read_json(json_path, [])
    return []


def finalize_artifacts(run_id: str) -> None:
    run_dir = get_run_dir(run_id)
    results = read_results(run_id)
    errors = [row for row in results if row.get("error")]
    write_json(run_dir / "results.json", results)
    write_json(run_dir / "errors.json", errors)
    write_results_csv(run_dir / "results.csv", results)
    write_comparison_md(run_dir / "comparison.md", results)
    bundle_path = run_dir / "run_bundle.zip"
    if bundle_path.exists():
        bundle_path.unlink()
    temp_base = run_dir.parent / f"{run_id}_bundle"
    temp_zip = Path(shutil.make_archive(str(temp_base), "zip", run_dir))
    if bundle_path.exists():
        bundle_path.unlink()
    temp_zip.replace(bundle_path)


def write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "run_id",
        "object_id",
        "object_name",
        "norad_cat_id",
        "country_code",
        "evidence_count",
        "model",
        "latency_ms",
        "baseline_summary",
        "llama_summary",
        "error",
        "has_unsupported_country",
        "has_unsupported_sensor",
        "mentions_missing_field",
        "ended_incomplete",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            eval_flags = result.get("eval") or {}
            writer.writerow(
                {
                    **{field: result.get(field) for field in fields},
                    "has_unsupported_country": eval_flags.get("has_unsupported_country"),
                    "has_unsupported_sensor": eval_flags.get("has_unsupported_sensor"),
                    "mentions_missing_field": eval_flags.get("mentions_missing_field"),
                    "ended_incomplete": eval_flags.get("ended_incomplete"),
                }
            )


def write_comparison_md(path: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Llama Summary Experiment Comparison",
        "",
        "| Satellite | Evidence | Baseline | Llama | Latency | Flags | Error |",
        "|---|---:|---|---|---:|---|---|",
    ]
    for row in results:
        eval_flags = row.get("eval") or {}
        flags = ", ".join(key for key, value in eval_flags.items() if value) or "none"
        satellite = f"{row.get('object_name')} ({row.get('norad_cat_id') or row.get('object_id')})"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(satellite),
                    str(row.get("evidence_count", "")),
                    _md(row.get("baseline_summary", "")),
                    _md(row.get("llama_summary", "")),
                    f"{row.get('latency_ms', 0):.1f}",
                    _md(flags),
                    _md(row.get("error") or ""),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _md(value: str) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|")
    return text[:1000]


def list_runs() -> list[dict[str, Any]]:
    runs_dir = get_runs_dir()
    if not runs_dir.exists():
        return []
    runs = []
    for child in sorted(runs_dir.iterdir(), reverse=True):
        if child.is_dir() and (child / "status.json").exists():
            runs.append(read_json(child / "status.json"))
    return runs
