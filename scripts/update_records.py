#!/usr/bin/env python3
"""Create a daily RecordWatch Canada snapshot from ECCC LTCE collections."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

API_ROOT = "https://api.weather.gc.ca/collections"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TIMEZONE = ZoneInfo("America/Toronto")

PROVINCE_NAMES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon"
}
REGIONS = {
    "Ontario": {"ON"}, "Prairies": {"AB", "SK", "MB"}, "Atlantic Canada": {"NB", "NS", "NL", "PE"},
    "British Columbia": {"BC"}, "Quebec": {"QC"}, "Northern Canada": {"YT", "NT", "NU"}
}
TYPE_LABELS = {
    "high_max": "record high maximum temperature", "high_min": "record high minimum temperature",
    "low_max": "record low maximum temperature", "low_min": "record low minimum temperature",
    "precipitation": "daily precipitation record", "snowfall": "daily snowfall record"
}

@dataclass(frozen=True)
class FieldMap:
    type: str
    record: str
    record_year: str
    previous: str
    previous_year: str
    begin: str
    unit: str

TEMPERATURE_FIELDS = [
    FieldMap("high_max", "RECORD_HIGH_MAX_TEMP", "RECORD_HIGH_MAX_TEMP_YR", "PREV_RECORD_HIGH_MAX_TEMP", "PREV_RECORD_HIGH_MAX_TEMP_YR", "MAX_TEMP_RECORD_BEGIN", "°C"),
    FieldMap("high_min", "RECORD_HIGH_MIN_TEMP", "RECORD_HIGH_MIN_TEMP_YR", "PREV_RECORD_HIGH_MIN_TEMP", "PREV_RECORD_HIGH_MIN_TEMP_YR", "MIN_TEMP_RECORD_BEGIN", "°C"),
    FieldMap("low_max", "RECORD_LOW_MAX_TEMP", "RECORD_LOW_MAX_TEMP_YR", "PREV_RECORD_LOW_MAX_TEMP", "PREV_RECORD_LOW_MAX_TEMP_YR", "MAX_TEMP_RECORD_BEGIN", "°C"),
    FieldMap("low_min", "RECORD_LOW_MIN_TEMP", "RECORD_LOW_MIN_TEMP_YR", "PREV_RECORD_LOW_MIN_TEMP", "PREV_RECORD_LOW_MIN_TEMP_YR", "MIN_TEMP_RECORD_BEGIN", "°C"),
]
PRECIP_FIELD = FieldMap("precipitation", "RECORD_PRECIPITATION", "RECORD_PRECIPITATION_YR", "PREV_RECORD_PRECIPITATION", "PREV_RECORD_PRECIPITATION_YR", "RECORD_BEGIN", "mm")
SNOW_FIELD = FieldMap("snowfall", "RECORD_SNOWFALL", "RECORD_SNOWFALL_YR", "PREV_RECORD_SNOWFALL", "PREV_RECORD_SNOWFALL_YR", "RECORD_BEGIN", "cm")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Climate date in YYYY-MM-DD. Defaults to yesterday in America/Toronto.")
    return parser.parse_args()


def target_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(TIMEZONE).date() - timedelta(days=1)


def fetch_collection(collection: str, target: date) -> dict[str, Any]:
    url = f"{API_ROOT}/{collection}/items"
    params = {
        "f": "json", "lang": "en", "limit": 1000,
        "filter": f"properties.LOCAL_MONTH={target.month} AND properties.LOCAL_DAY={target.day}",
    }
    response = requests.get(url, params=params, timeout=90, headers={"User-Agent": "RecordWatch-Canada/1.0"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("features"), list):
        raise ValueError(f"Unexpected response from {collection}")
    return payload


def year_from_date(value: Any, fallback: int) -> int:
    if not value:
        return fallback
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return fallback


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_record(feature: dict[str, Any], fields: FieldMap, target: date) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    record_year = props.get(fields.record_year)
    try:
        if int(record_year) != target.year:
            return None
    except (TypeError, ValueError):
        return None

    value = finite_number(props.get(fields.record))
    previous = finite_number(props.get(fields.previous))
    previous_year = props.get(fields.previous_year)
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if value is None or previous is None or not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
        
    # A zero precipitation or snowfall value is not a meaningful record event.
    if fields.type in {"precipitation", "snowfall"} and value <= 0:
        return None
        
    difference = round(value - previous, 2)
    tied = math.isclose(value, previous, abs_tol=0.049)
    province = str(props.get("PROVINCE_CODE") or "").upper()
    community = str(props.get("VIRTUAL_STATION_NAME_E") or "Unknown Area").title()
    begin_year = year_from_date(props.get(fields.begin), target.year)
    station_id = props.get("VIRTUAL_CLIMATE_ID") or feature.get("id") or community
    return {
        "id": f"{station_id}-{target.isoformat()}-{fields.type}",
        "date": target.isoformat(), "community": community, "province": province,
        "provinceName": PROVINCE_NAMES.get(province, province), "type": fields.type,
        "status": "tied" if tied else "broken", "value": value, "unit": fields.unit,
        "previousValue": previous, "previousYear": previous_year, "difference": difference,
        "recordBeginYear": begin_year, "periodYears": target.year - begin_year + 1,
        "coordinates": [float(coordinates[0]), float(coordinates[1])],
        "sourceUpdated": props.get("LAST_UPDATED"), "sourceId": feature.get("id")
    }


def process_features(payload: dict[str, Any], fields: list[FieldMap], target: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        for field in fields:
            record = normalize_record(feature, field, target)
            if record:
                records.append(record)
    return records


def choose_record_of_day(records: list[dict[str, Any]], target: date) -> dict[str, Any] | None:
    if not records:
        return None
    def score(record: dict[str, Any]) -> tuple[int, int, float]:
        previous_year = int(record.get("previousYear") or target.year)
        age = max(0, target.year - previous_year)
        margin = abs(float(record.get("difference") or 0))
        return (1 if record["status"] == "broken" else 0, age, margin)
    return max(records, key=score)


def build_highlights(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []

    for region, codes in REGIONS.items():
        group = [record for record in records if record["province"] in codes]

        if not group:
            continue

        total = len(group)
        communities = len({record["community"] for record in group})
        broken_count = sum(record["status"] == "broken" for record in group)
        tied_count = sum(record["status"] == "tied" for record in group)

        type_counts = Counter(record["type"] for record in group)
        leading_type, leading_count = type_counts.most_common(1)[0]

        record_word = "record" if total == 1 else "records"
        community_word = "community" if communities == 1 else "communities"

        status_text = (
            f"{total} {record_word} across {communities} {community_word}: "
            f"{broken_count} broken, {tied_count} tied."
        )

        if total == 1:
            type_text = f"It was a {TYPE_LABELS[leading_type]}."
        elif leading_count == total:
            type_text = (
                f"All {total} were {TYPE_LABELS[leading_type]} events."
            )
        else:
            type_text = (
                f"The most common type was {TYPE_LABELS[leading_type]} "
                f"({leading_count} of {total})."
            )

        output.append(
            {
                "region": region,
                "count": total,
                "brokenCount": broken_count,
                "tiedCount": tied_count,
                "leadingType": leading_type,
                "text": f"{status_text} {type_text}",
            }
        )

    return sorted(output, key=lambda item: item["count"], reverse=True)


def build_story(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {"description": "No new daily records were identified in the current source snapshot."}
    verb = "tied" if record["status"] == "tied" else "exceeded"
    return {"description": f"{record['community']}, {record['province']} recorded {record['value']:.1f} {record['unit']}. It {verb} the previous {TYPE_LABELS[record['type']]} of {record['previousValue']:.1f} {record['unit']} from {record['previousYear']}."}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_archive_index(target: date) -> None:
    path = DATA_DIR / "archive-index.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {"dates": []}
    dates = set(payload.get("dates", [])); dates.add(target.isoformat())
    write_json(path, {"dates": sorted(dates), "updated": datetime.now(TIMEZONE).isoformat(timespec="seconds")})


def main() -> int:
    target = target_date(parse_args().date)
    print(f"Building RecordWatch snapshot for {target}")
    temp = fetch_collection("ltce-temperature", target)
    precip = fetch_collection("ltce-precipitation", target)
    snow = fetch_collection("ltce-snowfall", target)

    records = process_features(temp, TEMPERATURE_FIELDS, target)
    records += process_features(precip, [PRECIP_FIELD], target)
    records += process_features(snow, [SNOW_FIELD], target)
    records.sort(key=lambda r: (r["province"], r["community"], r["type"]))

    record_of_day = choose_record_of_day(records, target)
    oldest_age = max((target.year - int(r["previousYear"]) for r in records if r.get("previousYear")), default=0)
    source_updates = [r["sourceUpdated"] for r in records if r.get("sourceUpdated")]
    payload = {
        "schemaVersion": 1, "date": target.isoformat(), "latestAvailableDate": target.isoformat(),
        "generatedAt": datetime.now(TIMEZONE).isoformat(timespec="seconds"), "sourceLastUpdated": max(source_updates, default="Not reported"),
        "source": "Environment and Climate Change Canada — MSC GeoMet LTCE", "isDemo": False,
        "summary": {"totalRecords": len(records), "communities": len({r['community'] for r in records}), "tiedRecords": sum(r['status'] == 'tied' for r in records), "oldestRecordAge": oldest_age},
        "recordOfDay": record_of_day, "story": build_story(record_of_day), "highlights": build_highlights(records), "records": records,
        "notes": ["Values may be revised by ECCC after initial publication.", "The archive contains daily snapshots saved by RecordWatch Canada after launch."]
    }
    archive_path = DATA_DIR / "archive" / f"{target.year:04d}" / f"{target.month:02d}" / f"{target.isoformat()}.json"
    write_json(archive_path, payload)
    latest_path = DATA_DIR / "latest.json"
    should_update_latest = True
    try:
        existing_latest = json.loads(latest_path.read_text(encoding="utf-8"))
        existing_date = date.fromisoformat(existing_latest.get("date", "1900-01-01"))
        should_update_latest = target >= existing_date or bool(existing_latest.get("isDemo"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    if should_update_latest:
        write_json(latest_path, payload)
    update_archive_index(target)
    print(f"Wrote {len(records)} records to {archive_path.relative_to(ROOT)}")
    if not should_update_latest:
        print("Archive saved without replacing the newer homepage snapshot.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"ECCC request failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        raise
