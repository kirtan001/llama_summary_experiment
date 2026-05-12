from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    source: str = Field(default="Unknown")
    description: str | None = None
    mission: list[str] = Field(default_factory=list)
    sensors: list[str] = Field(default_factory=list)
    purpose: str | None = None
    program: str | None = None
    link: str | None = None

    @field_validator("mission", "sensors", mode="before")
    @classmethod
    def clean_json_list(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("source", mode="before")
    @classmethod
    def clean_source(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text or "Unknown"

    @field_validator("description", "purpose", "program", "link", mode="before")
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SatelliteRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    object_id: str = Field(..., min_length=1)
    object_name: str = Field(..., min_length=1)
    norad_cat_id: int | None = None
    country_code: str | None = None
    evidence_count: int | None = None
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)

    @field_validator("country_code", mode="before")
    @classmethod
    def clean_country(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text.upper() if text else None

    @model_validator(mode="after")
    def set_evidence_count(self) -> "SatelliteRecord":
        if self.evidence_count is None:
            self.evidence_count = len(self.evidence_records)
        return self


class DatasetPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dataset_id: str = Field(default_factory=lambda: f"uploaded_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
    records: list[SatelliteRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_records(self) -> "DatasetPayload":
        if not self.records:
            raise ValueError("dataset must include at least one satellite record")
        return self


class RunConfig(BaseModel):
    model: str = "llama3.2:3b-instruct-q4_K_M"
    limit: int | None = Field(default=None, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=160, ge=16, le=2048)
    max_evidence_chars_per_record: int = Field(default=2000, ge=100, le=20000)


class CreateRunRequest(BaseModel):
    dataset: DatasetPayload
    config: RunConfig = Field(default_factory=RunConfig)


class EvalFlags(BaseModel):
    has_unsupported_country: bool = False
    has_unsupported_sensor: bool = False
    mentions_missing_field: bool = False
    ended_incomplete: bool = False


class LlamaParsedResponse(BaseModel):
    summary: str = ""
    evidence_used: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    warnings: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    run_id: str
    object_id: str
    object_name: str
    norad_cat_id: int | None = None
    country_code: str | None = None
    evidence_count: int = 0
    model: str
    prompt_hash: str
    baseline_summary: str
    llama_summary: str
    latency_ms: float
    eval: EvalFlags
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class RunStatus(BaseModel):
    run_id: str
    state: Literal["queued", "running", "stopping", "stopped", "completed", "failed"]
    dataset_id: str
    total: int
    completed: int = 0
    failed: int = 0
    average_latency_ms: float | None = None
    estimated_remaining_seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
