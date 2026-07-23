#!/usr/bin/env python3
"""Build a rolling seven-day RecordWatch Canada recap."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WEEKLY_DIR = DATA_DIR / "weekly"
TIMEZONE = ZoneInfo("America/Toronto")

TYPE_LABELS = {
    "high_max": "Record High Maximum Temperature",
    "high_min": "Record High Minimum Temperature",
    "low_max": "Record Low Maximum Temperature",
    "low_min": "Record Low Minimum Temperature",
    "precipitation": "Daily Precipitation Record",
    "snowfall": "Daily Snowfall Record",
}

PROVINCE_NAMES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def daily_archive_path(date_string: str) -> Path:
    year, month, _ = date_string.split("-")

    return (
        DATA_DIR
        / "archive"
        / year
        / month
        / f"{date_string}.json"
    )


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(record)

    community = str(cleaned.get("community") or "Unknown")

    if community.lower().endswith(" area"):
        community = community[:-5].rstrip()

    cleaned["community"] = community

    return cleaned


def record_age(record: dict[str, Any]) -> int:
    try:
        record_year = int(str(record["date"])[:4])
        previous_year = int(record["previousYear"])
    except (KeyError, TypeError, ValueError):
        return 0

    return max(0, record_year - previous_year)


def record_margin(record: dict[str, Any]) -> float:
    try:
        return abs(float(record.get("difference") or 0))
    except (TypeError, ValueError):
        return 0.0


def record_score(record: dict[str, Any]) -> tuple[int, int, float]:
    return (
        1 if record.get("status") == "broken" else 0,
        record_age(record),
        record_margin(record),
    )


def build_type_breakdown(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []

    for record_type, label in TYPE_LABELS.items():
        group = [
            record
            for record in records
            if record.get("type") == record_type
        ]

        if not group:
            continue

        output.append(
            {
                "type": record_type,
                "label": label,
                "total": len(group),
                "broken": sum(
                    record.get("status") == "broken"
                    for record in group
                ),
                "tied": sum(
                    record.get("status") == "tied"
                    for record in group
                ),
            }
        )

    return sorted(
        output,
        key=lambda item: item["total"],
        reverse=True,
    )


def build_province_breakdown(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = Counter(
        record.get("province")
        for record in records
        if record.get("province")
    )

    return [
        {
            "province": province,
            "provinceName": PROVINCE_NAMES.get(
                province,
                province,
            ),
            "total": total,
        }
        for province, total in counts.most_common()
    ]


def build_summary_text(
    records: list[dict[str, Any]],
    type_breakdown: list[dict[str, Any]],
    province_breakdown: list[dict[str, Any]],
) -> str:
    broken = sum(
        record.get("status") == "broken"
        for record in records
    )

    tied = sum(
        record.get("status") == "tied"
        for record in records
    )

    if not records:
        return (
            "No daily weather records were identified "
            "during the available seven-day period."
        )

    broken_word = "record" if broken == 1 else "records"

    status_text = f"{broken} {broken_word} broken"

    if tied > 0:
        status_text += f", {tied} tied"

    status_text += "."

    details = []

    if type_breakdown:
        leading_type = type_breakdown[0]

        details.append(
            f"{leading_type['label']} was the most common "
            f"category ({leading_type['total']} of "
            f"{len(records)} record events)."
        )

    if province_breakdown:
        leading_province = province_breakdown[0]

        details.append(
            f"{leading_province['provinceName']} recorded "
            f"the most events ({leading_province['total']})."
        )

    return " ".join([status_text, *details])


def main() -> int:
    archive_index_path = DATA_DIR / "archive-index.json"

    if not archive_index_path.exists():
        raise FileNotFoundError(
            "data/archive-index.json does not exist."
        )

    archive_index = read_json(archive_index_path)

    available_dates = sorted(
        {
            date.fromisoformat(value)
            for value in archive_index.get("dates", [])
        }
    )

    if not available_dates:
        raise ValueError(
            "The archive index contains no climate dates."
        )

    end_date = available_dates[-1]
    start_date = end_date - timedelta(days=6)

    included_dates = [
        value
        for value in available_dates
        if start_date <= value <= end_date
    ]

    records: list[dict[str, Any]] = []
    loaded_dates: list[str] = []

    for climate_date in included_dates:
        date_string = climate_date.isoformat()
        snapshot_path = daily_archive_path(date_string)

        if not snapshot_path.exists():
            continue

        snapshot = read_json(snapshot_path)

        records.extend(
            clean_record(record)
            for record in snapshot.get("records", [])
        )

        loaded_dates.append(date_string)

    broken_records = [
        record
        for record in records
        if record.get("status") == "broken"
    ]

    tied_records = [
        record
        for record in records
        if record.get("status") == "tied"
    ]

    type_breakdown = build_type_breakdown(records)
    province_breakdown = build_province_breakdown(records)

    notable_records = sorted(
        records,
        key=record_score,
        reverse=True,
    )[:6]

    for record in notable_records:
        record["previousRecordAge"] = record_age(record)

    payload = {
        "schemaVersion": 1,
        "title": "This Week in Canadian Extremes",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "datesIncluded": loaded_dates,
        "daysAvailable": len(loaded_dates),
        "isPartial": len(loaded_dates) < 7,
        "generatedAt": datetime.now(TIMEZONE).isoformat(
            timespec="seconds"
        ),
        "summary": {
            "totalRecords": len(records),
            "brokenRecords": len(broken_records),
            "tiedRecords": len(tied_records),
        },
        "summaryText": build_summary_text(
            records,
            type_breakdown,
            province_breakdown,
        ),
        "typeBreakdown": type_breakdown,
        "provinceBreakdown": province_breakdown,
        "notableRecords": notable_records,
        "notes": [
            (
                "This recap covers the latest seven "
                "completed climate dates."
            ),
            (
                "Values are preliminary and may be "
                "revised by Environment and Climate "
                "Change Canada."
            ),
        ],
    }

    write_json(
        WEEKLY_DIR / "latest.json",
        payload,
    )

    write_json(
        WEEKLY_DIR
        / "archive"
        / f"{end_date.year:04d}"
        / f"{end_date.isoformat()}.json",
        payload,
    )

    print(
        "Built weekly recap for "
        f"{start_date.isoformat()} through "
        f"{end_date.isoformat()} using "
        f"{len(loaded_dates)} daily snapshots and "
        f"{len(records)} records."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
