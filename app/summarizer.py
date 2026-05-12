from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

import requests

from app.schemas import LlamaParsedResponse, RunConfig, SatelliteRecord


PURPOSE_LABELS = {
    "A": "amateur, academic, or non-profit",
    "B": "business or commercial",
    "C": "civil government",
    "D": "defense, military, or intelligence",
    "AB": "amateur/academic and business",
    "AC": "amateur/academic and civil government",
    "AD": "amateur/academic and defense",
    "BC": "business and civil government",
    "BD": "business and defense",
    "CD": "civil government and defense",
}


def _first_nonempty(items: list[str]) -> str | None:
    for item in items:
        text = str(item or "").strip()
        if text:
            return text
    return None


def _unique_sorted(values: list[str | None]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _sentence_split(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]


def _best_evidence_sentence(record: SatelliteRecord) -> str | None:
    keywords = {
        "optical",
        "sar",
        "radar",
        "imaging",
        "reconnaissance",
        "defense",
        "military",
        "communications",
        "navigation",
        "meteorology",
        "surveillance",
        "payload",
        "sensor",
        "earth observation",
        "signals intelligence",
    }
    best = None
    best_score = -1
    for evidence in record.evidence_records:
        for sentence in _sentence_split(evidence.description or ""):
            sentence_l = sentence.lower()
            score = sum(sentence_l.count(keyword) for keyword in keywords)
            if len(sentence.split()) < 5:
                score -= 1
            if score > best_score:
                best = sentence
                best_score = score
    if best:
        return best[:500]
    return None


def build_baseline_summary(record: SatelliteRecord) -> str:
    evidence = record.evidence_records
    sources = _unique_sorted([ev.source for ev in evidence])
    sensors = _unique_sorted([sensor for ev in evidence for sensor in ev.sensors])
    missions = _unique_sorted([mission for ev in evidence for mission in ev.mission])
    programs = _unique_sorted([ev.program for ev in evidence])
    purposes = _unique_sorted([ev.purpose for ev in evidence])

    subject_bits = [record.object_name]
    if record.country_code:
        subject_bits.append(f"country {record.country_code}")
    if programs:
        subject_bits.append(f"program {', '.join(programs[:3])}")

    summary = f"{', '.join(subject_bits)} has {len(evidence)} evidence record(s)"
    if sources:
        summary += f" from {', '.join(sources[:6])}"
    summary += "."

    details: list[str] = []
    if sensors:
        details.append(f"reported sensors: {', '.join(sensors[:8])}")
    if missions:
        details.append(f"missions: {', '.join(missions[:8])}")
    if purposes:
        purpose_labels = [PURPOSE_LABELS.get(code, code) for code in purposes]
        details.append(f"purpose: {', '.join(purpose_labels[:5])}")
    if details:
        summary += " Available fields show " + "; ".join(details) + "."

    best_sentence = _best_evidence_sentence(record)
    if best_sentence:
        summary += f" Key evidence note: {best_sentence}"

    return summary


def _truncate_text(text: str | None, max_chars: int) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def build_prompt(record: SatelliteRecord, config: RunConfig) -> tuple[str, str]:
    evidence_payload: list[dict[str, Any]] = []
    for evidence in record.evidence_records:
        evidence_payload.append(
            {
                "id": evidence.id,
                "source": evidence.source,
                "description": _truncate_text(evidence.description, config.max_evidence_chars_per_record),
                "mission": evidence.mission,
                "sensors": evidence.sensors,
                "purpose": evidence.purpose,
                "program": evidence.program,
                "link": evidence.link,
            }
        )

    payload = {
        "object_id": record.object_id,
        "object_name": record.object_name,
        "norad_cat_id": record.norad_cat_id,
        "country_code": record.country_code,
        "evidence_count": len(record.evidence_records),
        "evidence_records": evidence_payload,
    }
    evidence_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    prompt = (
        "You are summarizing satellite evidence.\n"
        "Use only the provided JSON.\n"
        "Do not invent operator, launch date, sensor, mission, country, or purpose.\n"
        "If a value is missing, omit it or say \"not available\".\n"
        "Write 1-2 concise sentences.\n"
        "Mention source disagreement only if evidence conflicts.\n"
        "Return valid JSON exactly in this shape:\n"
        "{\n"
        "  \"summary\": \"...\",\n"
        "  \"evidence_used\": [\"evidence_id_1\"],\n"
        "  \"confidence\": \"high|medium|low\",\n"
        "  \"warnings\": []\n"
        "}\n\n"
        f"SATELLITE_EVIDENCE_JSON:\n{evidence_json}"
    )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, prompt_hash


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"summary": cleaned, "evidence_used": [], "confidence": "low", "warnings": ["non_json_response"]}


class OllamaSummarizer:
    def __init__(self, base_url: str | None = None, timeout: int | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.timeout = timeout or int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))

    def summarize(self, record: SatelliteRecord, config: RunConfig) -> dict[str, Any]:
        prompt, prompt_hash = build_prompt(record, config)
        started = time.perf_counter()
        payload = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": "You are a factual satellite evidence summarizer."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_output_tokens,
            },
        }
        response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        response.raise_for_status()
        raw = response.json()
        latency_ms = (time.perf_counter() - started) * 1000
        content = raw.get("message", {}).get("content", "")
        parsed = LlamaParsedResponse.model_validate(_extract_json_object(content))
        return {
            "prompt_hash": prompt_hash,
            "latency_ms": latency_ms,
            "parsed": parsed,
            "raw_response": raw,
        }
