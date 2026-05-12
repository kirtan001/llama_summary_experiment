from __future__ import annotations

import json
import os
import time
from typing import Any

import pandas as pd
import requests
import streamlit as st

from app.schemas import DatasetPayload, RunConfig


API_URL = os.getenv("SUMMARY_API_URL", "http://localhost:8010").rstrip("/")
ARTIFACTS = ["results.json", "results.csv", "comparison.md", "run.log", "errors.json", "run_bundle.zip"]


st.set_page_config(page_title="Llama Summary Experiment", layout="wide")
st.title("Llama Summary Experiment Lab")


def api_get(path: str, **kwargs: Any) -> requests.Response:
    response = requests.get(f"{API_URL}{path}", timeout=30, **kwargs)
    response.raise_for_status()
    return response


def api_post(path: str, payload: dict[str, Any] | None = None) -> requests.Response:
    response = requests.post(f"{API_URL}{path}", json=payload, timeout=60)
    response.raise_for_status()
    return response


def dataset_stats(dataset: DatasetPayload) -> dict[str, Any]:
    records = dataset.records
    missing_sensors = sum(1 for record in records if not any(ev.sensors for ev in record.evidence_records))
    missing_purpose = sum(1 for record in records if not any(ev.purpose for ev in record.evidence_records))
    missing_program = sum(1 for record in records if not any(ev.program for ev in record.evidence_records))
    evidence_counts = [len(record.evidence_records) for record in records]
    countries = sorted({record.country_code for record in records if record.country_code})
    return {
        "records": len(records),
        "total_evidence": sum(evidence_counts),
        "min_evidence": min(evidence_counts) if evidence_counts else 0,
        "max_evidence": max(evidence_counts) if evidence_counts else 0,
        "countries": len(countries),
        "missing_sensors": missing_sensors,
        "missing_purpose": missing_purpose,
        "missing_program": missing_program,
    }


with st.sidebar:
    st.header("Connection")
    st.caption(f"API: `{API_URL}`")
    try:
        health = api_get("/health").json()
        st.success(f"API {health['status']}")
    except Exception as exc:
        st.error(f"API unavailable: {exc}")

    st.header("Model Controls")
    model = st.text_input("Ollama model", value="llama3.2:3b-instruct-q4_K_M")
    limit = st.number_input("Limit records", min_value=1, value=100, step=1)
    use_all = st.checkbox("Use all uploaded records", value=False)
    temperature = st.slider("Temperature", min_value=0.0, max_value=2.0, value=0.0, step=0.1)
    max_output_tokens = st.number_input("Max output tokens", min_value=16, max_value=2048, value=160, step=16)
    max_evidence_chars = st.number_input("Max evidence chars per record", min_value=100, max_value=20000, value=2000, step=100)
    auto_refresh = st.checkbox("Auto refresh active run", value=True)


uploaded = st.file_uploader("Upload satellite batch JSON", type=["json"])
dataset: DatasetPayload | None = None

if uploaded:
    try:
        dataset_json = json.loads(uploaded.getvalue().decode("utf-8"))
        dataset = DatasetPayload.model_validate(dataset_json)
        st.session_state["dataset"] = dataset.model_dump(mode="json")
        st.success(f"Loaded dataset `{dataset.dataset_id}`")
    except Exception as exc:
        st.error(f"Invalid dataset JSON: {exc}")
elif "dataset" in st.session_state:
    dataset = DatasetPayload.model_validate(st.session_state["dataset"])


if dataset:
    stats = dataset_stats(dataset)
    cols = st.columns(7)
    cols[0].metric("Satellites", stats["records"])
    cols[1].metric("Evidence", stats["total_evidence"])
    cols[2].metric("Min evidence", stats["min_evidence"])
    cols[3].metric("Max evidence", stats["max_evidence"])
    cols[4].metric("Countries", stats["countries"])
    cols[5].metric("No sensors", stats["missing_sensors"])
    cols[6].metric("No purpose", stats["missing_purpose"])

    with st.expander("Preview uploaded records", expanded=False):
        preview_rows = [
            {
                "object_name": record.object_name,
                "object_id": record.object_id,
                "norad_cat_id": record.norad_cat_id,
                "country_code": record.country_code,
                "evidence_count": len(record.evidence_records),
            }
            for record in dataset.records[:100]
        ]
        st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

    run_config = RunConfig(
        model=model,
        limit=None if use_all else int(limit),
        temperature=float(temperature),
        max_output_tokens=int(max_output_tokens),
        max_evidence_chars_per_record=int(max_evidence_chars),
    )

    start_col, stop_col, resume_col, refresh_col = st.columns(4)
    with start_col:
        if st.button("Start run", type="primary"):
            payload = {"dataset": dataset.model_dump(mode="json"), "config": run_config.model_dump(mode="json")}
            try:
                response = api_post("/runs", payload).json()
                st.session_state["run_id"] = response["run_id"]
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to start run: {exc}")

    run_id = st.session_state.get("run_id")
    with stop_col:
        if st.button("Stop run", disabled=not run_id):
            try:
                api_post(f"/runs/{run_id}/stop")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to stop run: {exc}")
    with resume_col:
        if st.button("Resume failed/missing", disabled=not run_id):
            try:
                api_post(f"/runs/{run_id}/resume")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to resume run: {exc}")
    with refresh_col:
        if st.button("Refresh", disabled=not run_id):
            st.rerun()


run_id = st.session_state.get("run_id")
if run_id:
    st.divider()
    st.subheader(f"Run `{run_id}`")
    try:
        status = api_get(f"/runs/{run_id}/status").json()
        status_cols = st.columns(6)
        status_cols[0].metric("State", status["state"])
        status_cols[1].metric("Total", status["total"])
        status_cols[2].metric("Completed", status["completed"])
        status_cols[3].metric("Failed", status["failed"])
        avg_latency = status.get("average_latency_ms")
        status_cols[4].metric("Avg latency", f"{avg_latency:.0f} ms" if avg_latency else "-")
        eta = status.get("estimated_remaining_seconds")
        status_cols[5].metric("ETA", f"{eta:.0f} s" if eta else "-")
        if status.get("message"):
            st.caption(status["message"])

        tab_results, tab_logs, tab_downloads = st.tabs(["Results", "Logs", "Downloads"])
        with tab_results:
            results = api_get(f"/runs/{run_id}/results").json()
            if results:
                rows = []
                for item in results:
                    flags = item.get("eval") or {}
                    rows.append(
                        {
                            "satellite": item.get("object_name"),
                            "norad": item.get("norad_cat_id"),
                            "country": item.get("country_code"),
                            "evidence": item.get("evidence_count"),
                            "baseline_summary": item.get("baseline_summary"),
                            "llama_summary": item.get("llama_summary"),
                            "latency_ms": item.get("latency_ms"),
                            "flags": ", ".join(key for key, value in flags.items() if value) or "none",
                            "error": item.get("error"),
                        }
                    )
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.info("No results written yet.")

        with tab_logs:
            logs = api_get(f"/runs/{run_id}/logs").text
            st.text_area("Run log", value=logs, height=420)

        with tab_downloads:
            for artifact in ARTIFACTS:
                try:
                    response = api_get(f"/runs/{run_id}/download/{artifact}")
                    st.download_button(
                        label=f"Download {artifact}",
                        data=response.content,
                        file_name=artifact,
                        mime="application/octet-stream",
                    )
                except Exception:
                    st.caption(f"{artifact} is not ready yet.")

        if auto_refresh and status["state"] in {"queued", "running", "stopping"}:
            time.sleep(2)
            st.rerun()
    except Exception as exc:
        st.error(f"Could not load run: {exc}")
else:
    st.info("Upload a batch JSON and start a run.")
