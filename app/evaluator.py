from __future__ import annotations

import re

from app.schemas import EvalFlags, SatelliteRecord


COUNTRY_TERMS = {
    "US": {"US", "USA", "UNITED STATES", "AMERICAN"},
    "PRC": {"PRC", "CHINA", "CHINESE"},
    "CIS": {"CIS", "RUSSIA", "RUSSIAN", "SOVIET", "USSR"},
    "JPN": {"JPN", "JAPAN", "JAPANESE"},
    "GER": {"GER", "GERMANY", "GERMAN"},
    "RWA": {"RWA", "RWANDA", "RWANDAN"},
    "IND": {"IND", "INDIA", "INDIAN"},
    "ESA": {"ESA", "EUROPEAN SPACE AGENCY"},
}

SENSOR_TERMS = {
    "OPTICAL",
    "OPTICAL-ISL",
    "COMM",
    "SAR",
    "RADAR",
    "IR",
    "HYPERSPECTRAL",
    "AIS",
    "ALTIMETER",
    "RADIOMETER",
    "SCATTEROMETER",
    "SIGINT",
}


def _all_input_text(record: SatelliteRecord) -> str:
    parts = [
        record.object_id,
        record.object_name,
        record.country_code or "",
    ]
    for evidence in record.evidence_records:
        parts.extend(
            [
                evidence.id,
                evidence.source,
                evidence.description or "",
                evidence.purpose or "",
                evidence.program or "",
                " ".join(evidence.sensors),
                " ".join(evidence.mission),
            ]
        )
    return " ".join(parts).upper()


def _contains_term(text: str, term: str) -> bool:
    if "-" in term or " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def evaluate_summary(record: SatelliteRecord, summary: str) -> EvalFlags:
    summary_upper = summary.upper()
    input_upper = _all_input_text(record)
    allowed_country_terms = set()
    if record.country_code:
        allowed_country_terms.update(COUNTRY_TERMS.get(record.country_code.upper(), {record.country_code.upper()}))

    has_unsupported_country = False
    for country_code, terms in COUNTRY_TERMS.items():
        if country_code == (record.country_code or "").upper():
            continue
        for term in terms:
            if _contains_term(summary_upper, term) and not _contains_term(input_upper, term):
                has_unsupported_country = True
                break
        if has_unsupported_country:
            break

    present_sensors = {sensor.upper() for ev in record.evidence_records for sensor in ev.sensors}
    has_unsupported_sensor = False
    for sensor in SENSOR_TERMS:
        if _contains_term(summary_upper, sensor) and sensor not in present_sensors and not _contains_term(input_upper, sensor):
            has_unsupported_sensor = True
            break

    mentions_missing_field = any(token in summary_upper for token in [" N/A", "UNKNOWN", "NULL", "NONE PROVIDED"])
    ended_incomplete = bool(summary.strip()) and summary.strip()[-1] not in ".!?"

    return EvalFlags(
        has_unsupported_country=has_unsupported_country,
        has_unsupported_sensor=has_unsupported_sensor,
        mentions_missing_field=mentions_missing_field,
        ended_incomplete=ended_incomplete,
    )
