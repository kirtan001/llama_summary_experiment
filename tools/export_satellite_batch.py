from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
SAT_LIBRARY_DIR = REPO_ROOT / "sat_library"
if str(SAT_LIBRARY_DIR) not in sys.path:
    sys.path.insert(0, str(SAT_LIBRARY_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def setup_django() -> None:
    import django

    django.setup()


def evidence_to_dict(evidence) -> dict:
    return {
        "id": evidence.id,
        "source": evidence.source or "Unknown",
        "description": evidence.description or "",
        "mission": evidence.mission if isinstance(evidence.mission, list) else [],
        "sensors": evidence.sensors if isinstance(evidence.sensors, list) else [],
        "purpose": evidence.purpose,
        "program": evidence.program,
        "link": evidence.link,
    }


def satellite_to_dict(satellite) -> dict:
    evidence_records = [
        evidence_to_dict(evidence)
        for evidence in satellite.evidence_records.all().order_by("source", "id")
    ]
    return {
        "object_id": satellite.object_id,
        "object_name": satellite.object_name,
        "norad_cat_id": satellite.norad_cat_id,
        "country_code": satellite.country_code,
        "evidence_count": len(evidence_records),
        "evidence_records": evidence_records,
    }


def base_queryset():
    from django.db.models import Count, Prefetch
    from api.models import Evidence, Satellite

    evidence_qs = Evidence.objects.only(
        "id",
        "source",
        "description",
        "mission",
        "sensors",
        "purpose",
        "program",
        "link",
    )
    return (
        Satellite.objects.annotate(evidence_count=Count("evidence_records", distinct=True))
        .filter(evidence_count__gt=0)
        .prefetch_related(Prefetch("evidence_records", queryset=evidence_qs))
    )


def select_random(limit: int):
    return list(base_queryset().order_by("?")[:limit])


def select_top_evidence(limit: int):
    return list(base_queryset().order_by("-evidence_count", "object_name")[:limit])


def select_diverse(limit: int):
    selected = []
    seen = set()
    targets = [
        ("one evidence", base_queryset().filter(evidence_count=1), max(1, limit // 4)),
        ("two evidence", base_queryset().filter(evidence_count=2), max(1, limit // 3)),
        ("three to five evidence", base_queryset().filter(evidence_count__gte=3), max(1, limit // 4)),
    ]

    for _, queryset, count in targets:
        for satellite in queryset.order_by("?")[:count]:
            if satellite.object_id not in seen:
                selected.append(satellite)
                seen.add(satellite.object_id)

    if len(selected) < limit:
        country_candidates = list(base_queryset().exclude(object_id__in=seen).order_by("country_code", "?")[: limit * 3])
        random.shuffle(country_candidates)
        for satellite in country_candidates:
            if satellite.object_id in seen:
                continue
            selected.append(satellite)
            seen.add(satellite.object_id)
            if len(selected) >= limit:
                break

    if len(selected) < limit:
        for satellite in base_queryset().exclude(object_id__in=seen).order_by("?")[: limit - len(selected)]:
            selected.append(satellite)
            seen.add(satellite.object_id)

    return selected[:limit]


def write_dataset(satellites: Iterable, output: Path, strategy: str, limit: int) -> None:
    records = [satellite_to_dict(satellite) for satellite in satellites]
    payload = {
        "dataset_id": f"sat_batch_{len(records)}_{strategy}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(records)} satellite records to {output}")
    if len(records) < limit:
        print(f"Warning: requested {limit}, exported {len(records)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export satellite evidence batches for Llama summary experiments.")
    parser.add_argument("--limit", type=int, default=100, help="Number of satellite records to export.")
    parser.add_argument(
        "--strategy",
        choices=["diverse", "random", "top-evidence"],
        default="diverse",
        help="Selection strategy.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "llama_summary_experiment" / "data" / "sat_batch_100.json",
        help="Output JSON path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_django()
    if args.strategy == "random":
        satellites = select_random(args.limit)
    elif args.strategy == "top-evidence":
        satellites = select_top_evidence(args.limit)
    else:
        satellites = select_diverse(args.limit)
    write_dataset(satellites, args.output, args.strategy, args.limit)


if __name__ == "__main__":
    main()
