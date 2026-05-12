from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.evaluator import evaluate_summary
from app.schemas import CreateRunRequest, DatasetPayload, RunConfig, RunResult, RunStatus
from app.storage import (
    append_log,
    append_result,
    create_run,
    finalize_artifacts,
    get_run_dir,
    list_runs,
    read_json,
    read_log,
    read_results,
    read_status,
    utc_now_iso,
    write_status,
)
from app.summarizer import OllamaSummarizer, build_baseline_summary, build_prompt


app = FastAPI(title="Llama Summary Experiment API", version="0.1.0")

_threads: dict[str, Thread] = {}
_stop_events: dict[str, Event] = {}
_lock = Lock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "llama-summary-experiment"}


@app.get("/runs")
def runs() -> list[dict[str, Any]]:
    return list_runs()


@app.post("/runs")
def start_run(request: CreateRunRequest) -> dict[str, str]:
    run_id, _ = create_run(request.dataset, request.config)
    _start_worker(run_id, request.dataset, request.config, resume_failed=False)
    return {"run_id": run_id}


@app.post("/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, str]:
    event = _stop_events.get(run_id)
    if event is None:
        raise HTTPException(status_code=404, detail="run is not active")
    event.set()
    status = read_status(run_id)
    status.state = "stopping"
    status.message = "Stop requested"
    write_status(run_id, status)
    append_log(run_id, "Stop requested")
    return {"run_id": run_id, "state": "stopping"}


@app.post("/runs/{run_id}/resume")
def resume_run(run_id: str) -> dict[str, str]:
    run_dir = get_run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    dataset = DatasetPayload.model_validate(read_json(run_dir / "input.json"))
    config = RunConfig.model_validate(read_json(run_dir / "config.json"))
    _start_worker(run_id, dataset, config, resume_failed=True)
    return {"run_id": run_id, "state": "running"}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, Any]:
    run_dir = get_run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return read_status(run_id).model_dump(mode="json")


@app.get("/runs/{run_id}/logs", response_class=PlainTextResponse)
def run_logs(run_id: str) -> str:
    run_dir = get_run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return read_log(run_id)


@app.get("/runs/{run_id}/results")
def run_results(run_id: str) -> list[dict[str, Any]]:
    run_dir = get_run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return read_results(run_id)


@app.get("/runs/{run_id}/download/{artifact_name}")
def download_artifact(run_id: str, artifact_name: str) -> FileResponse:
    allowed = {
        "results.json",
        "results.csv",
        "comparison.md",
        "run.log",
        "run_bundle.zip",
        "errors.json",
        "input.json",
        "config.json",
    }
    if artifact_name not in allowed:
        raise HTTPException(status_code=400, detail="unsupported artifact")
    path = get_run_dir(run_id) / artifact_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact not ready")
    return FileResponse(path, filename=artifact_name)


def _start_worker(run_id: str, dataset: DatasetPayload, config: RunConfig, resume_failed: bool) -> None:
    with _lock:
        existing = _threads.get(run_id)
        if existing and existing.is_alive():
            raise HTTPException(status_code=409, detail="run is already active")
        stop_event = Event()
        _stop_events[run_id] = stop_event
        thread = Thread(
            target=_run_batch,
            args=(run_id, dataset, config, stop_event, resume_failed),
            daemon=True,
        )
        _threads[run_id] = thread
        thread.start()


def _run_batch(run_id: str, dataset: DatasetPayload, config: RunConfig, stop_event: Event, resume_failed: bool) -> None:
    records = dataset.records[: config.limit] if config.limit else dataset.records
    existing_by_object_id = {row.get("object_id"): row for row in read_results(run_id)}
    if resume_failed:
        records = [
            record
            for record in records
            if record.object_id not in existing_by_object_id or existing_by_object_id[record.object_id].get("error")
        ]

    status = read_status(run_id)
    status.state = "running"
    status.started_at = status.started_at or utc_now_iso()
    status.total = len(records)
    status.completed = 0
    status.failed = 0
    status.message = "Run started"
    write_status(run_id, status)
    append_log(run_id, f"Run started with {len(records)} record(s), model={config.model}")

    summarizer = OllamaSummarizer()
    latencies: list[float] = []

    try:
        for index, record in enumerate(records, start=1):
            if stop_event.is_set():
                status.state = "stopped"
                status.finished_at = utc_now_iso()
                status.message = "Run stopped by user"
                write_status(run_id, status)
                append_log(run_id, "Run stopped by user")
                finalize_artifacts(run_id)
                return

            append_log(run_id, f"[{index}/{len(records)}] Summarizing {record.object_name} ({record.object_id})")
            result = _summarize_record(run_id, record, config, summarizer)
            append_result(run_id, result)

            if result.error:
                status.failed += 1
                append_log(run_id, f"[{index}/{len(records)}] Failed {record.object_id}: {result.error}")
            else:
                status.completed += 1
                latencies.append(result.latency_ms)
                append_log(run_id, f"[{index}/{len(records)}] Completed {record.object_id} in {result.latency_ms:.1f} ms")

            done = status.completed + status.failed
            status.average_latency_ms = sum(latencies) / len(latencies) if latencies else None
            if status.average_latency_ms and done:
                remaining = max(status.total - done, 0)
                status.estimated_remaining_seconds = remaining * status.average_latency_ms / 1000
            status.message = f"{done}/{status.total} processed"
            write_status(run_id, status)

        status.state = "completed" if status.failed == 0 else "failed"
        status.finished_at = datetime.now(timezone.utc).isoformat()
        status.message = "Run completed" if status.failed == 0 else "Run completed with errors"
        write_status(run_id, status)
        append_log(run_id, status.message)
        finalize_artifacts(run_id)
    except Exception as exc:
        status.state = "failed"
        status.finished_at = utc_now_iso()
        status.message = str(exc)
        write_status(run_id, status)
        append_log(run_id, f"Fatal run failure: {exc}\n{traceback.format_exc()}")
        finalize_artifacts(run_id)
    finally:
        _stop_events.pop(run_id, None)


def _summarize_record(run_id: str, record: Any, config: RunConfig, summarizer: OllamaSummarizer) -> RunResult:
    baseline = build_baseline_summary(record)
    _, prompt_hash = build_prompt(record, config)
    try:
        generated = summarizer.summarize(record, config)
        parsed = generated["parsed"]
        summary = parsed.summary.strip()
        eval_flags = evaluate_summary(record, summary)
        return RunResult(
            run_id=run_id,
            object_id=record.object_id,
            object_name=record.object_name,
            norad_cat_id=record.norad_cat_id,
            country_code=record.country_code,
            evidence_count=len(record.evidence_records),
            model=config.model,
            prompt_hash=generated["prompt_hash"],
            baseline_summary=baseline,
            llama_summary=summary,
            latency_ms=generated["latency_ms"],
            eval=eval_flags,
            raw_response={
                "parsed": parsed.model_dump(mode="json"),
                "ollama": generated["raw_response"],
            },
        )
    except Exception as exc:
        eval_flags = evaluate_summary(record, "")
        return RunResult(
            run_id=run_id,
            object_id=record.object_id,
            object_name=record.object_name,
            norad_cat_id=record.norad_cat_id,
            country_code=record.country_code,
            evidence_count=len(record.evidence_records),
            model=config.model,
            prompt_hash=prompt_hash,
            baseline_summary=baseline,
            llama_summary="",
            latency_ms=0,
            eval=eval_flags,
            raw_response={},
            error=str(exc),
        )
