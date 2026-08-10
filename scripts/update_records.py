#!/usr/bin/env python3
"""Create a daily RecordWatch Canada snapshot from ECCC LTCE collections.

RecordWatch validation policy:
- Source values are never silently corrected or rescaled.
- Structurally invalid candidate records are withheld from publication.
- Extraordinary temperature, precipitation, and snowfall values are withheld
  for manual verification rather than automatically declared false.
- A specifically verified outlier can be approved by record ID in
  data/validation-overrides.json. Approval never bypasses structural checks.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
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

# Review thresholds are intentionally conservative. Crossing one of these
# thresholds does NOT mean a value is impossible; it means RecordWatch should
# withhold it until it is independently verified and explicitly approved.
PRECIP_REVIEW_ABSOLUTE_MM = 150.0
PRECIP_REVIEW_RELATIVE_MIN_MM = 75.0
PRECIP_REVIEW_RATIO = 3.0
PRECIP_REVIEW_MARGIN_MM = 50.0

TEMP_REVIEW_HIGH_C = 50.0
TEMP_REVIEW_LOW_C = -65.0
TEMP_REVIEW_MARGIN_C = 15.0

SNOW_REVIEW_ABSOLUTE_CM = 150.0
SNOW_REVIEW_RELATIVE_MIN_CM = 75.0
SNOW_REVIEW_RATIO = 3.0
SNOW_REVIEW_MARGIN_CM = 50.0

# Broad geographic guardrails for Canadian point locations. These are wider
# than Canada's normal station footprint so ordinary coastal/border stations
# are not accidentally rejected.
CANADA_LONGITUDE_MIN = -142.0
CANADA_LONGITUDE_MAX = -50.0
CANADA_LATITUDE_MIN = 40.0
CANADA_LATITUDE_MAX = 85.0

MIN_VALID_YEAR = 1800
RECORD_TOLERANCE = 0.051

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
HIGHER_IS_RECORD = {"high_max", "high_min", "precipitation", "snowfall"}
LOWER_IS_RECORD = {"low_max", "low_min"}
TEMPERATURE_TYPES = {"high_max", "high_min", "low_max", "low_min"}


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
    """Fetch an ECCC LTCE collection with automatic retry protection."""
    url = f"{API_ROOT}/{collection}/items"
    params = {
        "f": "json",
        "lang": "en",
        "limit": 1000,
        "filter": (
            f"properties.LOCAL_MONTH={target.month} "
            f"AND properties.LOCAL_DAY={target.day}"
        ),
    }

    retry_delays = (10, 30)
    total_attempts = 3
    for attempt in range(1, total_attempts + 1):
        try:
            print(
                f"Fetching {collection} for {target} "
                f"(attempt {attempt}/{total_attempts})"
            )
            response = requests.get(
                url,
                params=params,
                timeout=90,
                headers={"User-Agent": "RecordWatch-Canada/1.2"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload.get("features"), list):
                raise ValueError(f"Unexpected response from {collection}")
            if attempt > 1:
                print(
                    f"::notice title=ECCC request recovered::"
                    f"{collection} succeeded on attempt {attempt}."
                )
            return payload
        except (requests.RequestException, ValueError) as exc:
            if attempt >= total_attempts:
                print(
                    f"::error title=ECCC request failed::"
                    f"{collection} failed after {total_attempts} attempts: {exc}"
                )
                raise
            delay = retry_delays[attempt - 1]
            print(
                f"::warning title=ECCC request retry::"
                f"{collection} attempt {attempt} failed: {exc}. "
                f"Retrying in {delay} seconds."
            )
            time.sleep(delay)

    raise RuntimeError(f"Unable to retrieve {collection} for {target}")


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

    if value is None or previous is None:
        print(
            f"::warning title=Malformed ECCC record skipped::"
            f"sourceId={feature.get('id')} | {fields.type} | "
            "record value or previous record is non-numeric"
        )
        return None

    if not isinstance(coordinates, list) or len(coordinates) < 2:
        print(
            f"::warning title=Malformed ECCC record skipped::"
            f"sourceId={feature.get('id')} | {fields.type} | missing coordinates"
        )
        return None

    longitude = finite_number(coordinates[0])
    latitude = finite_number(coordinates[1])
    if longitude is None or latitude is None:
        print(
            f"::warning title=Malformed ECCC record skipped::"
            f"sourceId={feature.get('id')} | {fields.type} | non-numeric coordinates"
        )
        return None

    # A zero precipitation or snowfall value is not a meaningful record event.
    if fields.type in {"precipitation", "snowfall"} and value <= 0:
        return None

    difference = round(value - previous, 2)
    tied = math.isclose(value, previous, abs_tol=0.049)
    province = str(props.get("PROVINCE_CODE") or "").upper()
    community = str(props.get("VIRTUAL_STATION_NAME_E") or "Unknown").title()
    if community.lower().endswith(" area"):
        community = community[:-5].rstrip()
    begin_year = year_from_date(props.get(fields.begin), target.year)
    station_id = props.get("VIRTUAL_CLIMATE_ID") or feature.get("id") or community

    return {
        "id": f"{station_id}-{target.isoformat()}-{fields.type}",
        "date": target.isoformat(),
        "community": community,
        "province": province,
        "provinceName": PROVINCE_NAMES.get(province, province),
        "type": fields.type,
        "status": "tied" if tied else "broken",
        "value": value,
        "unit": fields.unit,
        "previousValue": previous,
        "previousYear": previous_year,
        "difference": difference,
        "recordBeginYear": begin_year,
        "periodYears": target.year - begin_year + 1,
        "coordinates": [longitude, latitude],
        "sourceUpdated": props.get("LAST_UPDATED"),
        "sourceId": feature.get("id"),
    }


def process_features(payload: dict[str, Any], fields: list[FieldMap], target: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        for field in fields:
            record = normalize_record(feature, field, target)
            if record:
                records.append(record)
    return records


def load_validation_overrides() -> tuple[set[str], set[str]]:
    """Return (approved_ids, rejected_ids)."""
    path = DATA_DIR / "validation-overrides.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), set()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc

    approved = {str(value) for value in payload.get("approved", [])}
    rejected = {str(value) for value in payload.get("rejected", [])}
    overlap = approved & rejected
    if overlap:
        raise ValueError(
            "The same record ID cannot be both approved and rejected in "
            f"data/validation-overrides.json: {sorted(overlap)}"
        )
    return approved, rejected


def structural_validation_reason(record: dict[str, Any], target: date) -> str | None:
    """Return a hard validation failure reason, or None if structurally valid."""
    record_id = str(record.get("id") or "").strip()
    source_id = str(record.get("sourceId") or "").strip()
    record_type = str(record.get("type") or "")
    community = str(record.get("community") or "").strip()
    province = str(record.get("province") or "").upper()

    if not record_id:
        return "missing RecordWatch record ID"
    if not source_id:
        return "missing ECCC source feature ID"
    if record.get("date") != target.isoformat():
        return f"record date {record.get('date')!r} does not match climate date {target}"
    if record_type not in TYPE_LABELS:
        return f"unknown record type {record_type!r}"
    if not community or community.lower() == "unknown":
        return "missing or unknown community name"
    if province not in PROVINCE_NAMES:
        return f"invalid province/territory code {province!r}"
    if record.get("status") not in {"broken", "tied"}:
        return f"invalid record status {record.get('status')!r}"

    value = finite_number(record.get("value"))
    previous = finite_number(record.get("previousValue"))
    if value is None or previous is None:
        return "record value or previous record is non-numeric"

    coordinates = record.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return "coordinates are missing or malformed"
    longitude = finite_number(coordinates[0])
    latitude = finite_number(coordinates[1])
    if longitude is None or latitude is None:
        return "coordinates are non-numeric"
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        return f"coordinates are outside valid geographic ranges ({longitude}, {latitude})"
    if not (
        CANADA_LONGITUDE_MIN <= longitude <= CANADA_LONGITUDE_MAX
        and CANADA_LATITUDE_MIN <= latitude <= CANADA_LATITUDE_MAX
    ):
        return f"coordinates fall outside broad Canadian bounds ({longitude}, {latitude})"

    try:
        previous_year = int(record.get("previousYear"))
    except (TypeError, ValueError):
        return f"previous record year {record.get('previousYear')!r} is not an integer"
    if not (MIN_VALID_YEAR <= previous_year < target.year):
        return f"previous record year {previous_year} is outside the valid historical range"

    try:
        begin_year = int(record.get("recordBeginYear"))
    except (TypeError, ValueError):
        return f"record begin year {record.get('recordBeginYear')!r} is not an integer"
    if not (MIN_VALID_YEAR <= begin_year <= target.year):
        return f"record begin year {begin_year} is outside the valid range"
    if begin_year > previous_year:
        return f"record begin year {begin_year} is later than previous record year {previous_year}"

    expected_period = target.year - begin_year + 1
    try:
        period_years = int(record.get("periodYears"))
    except (TypeError, ValueError):
        return f"periodYears {record.get('periodYears')!r} is not an integer"
    if period_years != expected_period or period_years <= 0:
        return f"periodYears {period_years} is inconsistent with record begin year {begin_year}"

    difference = finite_number(record.get("difference"))
    if difference is None or not math.isclose(difference, round(value - previous, 2), abs_tol=0.011):
        return "stored record difference is inconsistent with value and previousValue"

    if record_type in HIGHER_IS_RECORD and value + RECORD_TOLERANCE < previous:
        return (
            f"{record_type} value {value} is lower than previous record {previous}; "
            "record direction is inconsistent"
        )
    if record_type in LOWER_IS_RECORD and value - RECORD_TOLERANCE > previous:
        return (
            f"{record_type} value {value} is higher than previous record {previous}; "
            "record direction is inconsistent"
        )

    is_tied = math.isclose(value, previous, abs_tol=0.049)
    expected_status = "tied" if is_tied else "broken"
    if record.get("status") != expected_status:
        return (
            f"record status {record.get('status')!r} is inconsistent with values; "
            f"expected {expected_status!r}"
        )

    return None


def precipitation_review_reason(record: dict[str, Any]) -> str | None:
    if record.get("type") != "precipitation":
        return None
    value = finite_number(record.get("value"))
    previous = finite_number(record.get("previousValue"))
    if value is None or previous is None:
        return "precipitation value or previous record is non-numeric"
    if value >= PRECIP_REVIEW_ABSOLUTE_MM:
        return (
            f"precipitation {value:.1f} mm meets the "
            f"{PRECIP_REVIEW_ABSOLUTE_MM:.0f} mm manual-review threshold"
        )
    margin = value - previous
    ratio = value / previous if previous > 0 else math.inf
    if (
        value >= PRECIP_REVIEW_RELATIVE_MIN_MM
        and ratio >= PRECIP_REVIEW_RATIO
        and margin >= PRECIP_REVIEW_MARGIN_MM
    ):
        return (
            f"precipitation {value:.1f} mm is an extreme jump over the previous "
            f"record {previous:.1f} mm (ratio {ratio:.2f}, margin {margin:.1f} mm)"
        )
    return None


def temperature_review_reason(record: dict[str, Any]) -> str | None:
    if record.get("type") not in TEMPERATURE_TYPES:
        return None
    value = finite_number(record.get("value"))
    previous = finite_number(record.get("previousValue"))
    if value is None or previous is None:
        return "temperature value or previous record is non-numeric"
    if value >= TEMP_REVIEW_HIGH_C:
        return f"temperature {value:.1f} °C meets the {TEMP_REVIEW_HIGH_C:.0f} °C high manual-review threshold"
    if value <= TEMP_REVIEW_LOW_C:
        return f"temperature {value:.1f} °C meets the {TEMP_REVIEW_LOW_C:.0f} °C low manual-review threshold"
    margin = abs(value - previous)
    if margin >= TEMP_REVIEW_MARGIN_C:
        return (
            f"temperature {value:.1f} °C differs from the previous record "
            f"{previous:.1f} °C by {margin:.1f} °C"
        )
    return None


def snowfall_review_reason(record: dict[str, Any]) -> str | None:
    if record.get("type") != "snowfall":
        return None
    value = finite_number(record.get("value"))
    previous = finite_number(record.get("previousValue"))
    if value is None or previous is None:
        return "snowfall value or previous record is non-numeric"
    if value >= SNOW_REVIEW_ABSOLUTE_CM:
        return (
            f"snowfall {value:.1f} cm meets the "
            f"{SNOW_REVIEW_ABSOLUTE_CM:.0f} cm manual-review threshold"
        )
    margin = value - previous
    ratio = value / previous if previous > 0 else math.inf
    if (
        value >= SNOW_REVIEW_RELATIVE_MIN_CM
        and ratio >= SNOW_REVIEW_RATIO
        and margin >= SNOW_REVIEW_MARGIN_CM
    ):
        return (
            f"snowfall {value:.1f} cm is an extreme jump over the previous "
            f"record {previous:.1f} cm (ratio {ratio:.2f}, margin {margin:.1f} cm)"
        )
    return None


def manual_review_reason(record: dict[str, Any]) -> str | None:
    return (
        precipitation_review_reason(record)
        or temperature_review_reason(record)
        or snowfall_review_reason(record)
    )


def validate_records(
    records: list[dict[str, Any]], target: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply fail-closed structural and outlier validation before publication."""
    approved_ids, rejected_ids = load_validation_overrides()
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for record in records:
        record_id = str(record.get("id") or "")
        category = "review"

        if record_id in seen_ids:
            reason = "duplicate RecordWatch record ID in the same climate-date snapshot"
            category = "invalid"
        else:
            seen_ids.add(record_id)
            if record_id in rejected_ids:
                reason = "record ID is explicitly rejected in data/validation-overrides.json"
                category = "invalid"
            else:
                # Hard checks ALWAYS run, even for manually approved outliers.
                reason = structural_validation_reason(record, target)
                if reason:
                    category = "invalid"
                elif record_id in approved_ids:
                    reason = None
                else:
                    reason = manual_review_reason(record)
                    category = "review"

        if reason:
            quarantined.append({
                "record": record,
                "reason": reason,
                "category": category,
            })
            print(
                "::warning title=RecordWatch record quarantined::"
                f"{record.get('community')}, {record.get('province')} | "
                f"{record.get('type')} | {record.get('value')} {record.get('unit')} | "
                f"sourceId={record.get('sourceId')} | category={category} | {reason}"
            )
        else:
            accepted.append(record)

    return accepted, quarantined


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
        broken_count = sum(record["status"] == "broken" for record in group)
        tied_count = sum(record["status"] == "tied" for record in group)
        type_counts = Counter(record["type"] for record in group)
        leading_type, leading_count = type_counts.most_common(1)[0]
        broken_word = "record" if broken_count == 1 else "records"
        if tied_count > 0:
            status_text = f"{broken_count} {broken_word} broken, {tied_count} tied."
        else:
            status_text = f"{broken_count} {broken_word} broken."
        if total == 1:
            type_text = f"It was a {TYPE_LABELS[leading_type]}."
        elif leading_count == total:
            type_text = f"All {total} were {TYPE_LABELS[leading_type]} events."
        else:
            type_text = f"The most common type was {TYPE_LABELS[leading_type]} ({leading_count} of {total})."
        output.append({
            "region": region,
            "count": total,
            "brokenCount": broken_count,
            "tiedCount": tied_count,
            "leadingType": leading_type,
            "text": f"{status_text} {type_text}",
        })

    return sorted(output, key=lambda item: item["count"], reverse=True)


def build_story(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {"description": "No new daily records were identified in the current source snapshot."}
    verb = "tied" if record["status"] == "tied" else "exceeded"
    return {
        "description": (
            f"{record['community']}, {record['province']} recorded {record['value']:.1f} {record['unit']}. "
            f"It {verb} the previous {TYPE_LABELS[record['type']]} of "
            f"{record['previousValue']:.1f} {record['unit']} from {record['previousYear']}."
        )
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_archive_index(target: date) -> None:
    path = DATA_DIR / "archive-index.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {"dates": []}
    dates = set(payload.get("dates", []))
    dates.add(target.isoformat())
    write_json(path, {
        "dates": sorted(dates),
        "updated": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
    })


def main() -> int:
    target = target_date(parse_args().date)
    print(f"Building RecordWatch snapshot for {target}")

    temp = fetch_collection("ltce-temperature", target)
    precip = fetch_collection("ltce-precipitation", target)
    snow = fetch_collection("ltce-snowfall", target)

    candidates = process_features(temp, TEMPERATURE_FIELDS, target)
    candidates += process_features(precip, [PRECIP_FIELD], target)
    candidates += process_features(snow, [SNOW_FIELD], target)

    # Crucial publication gate: suspicious or malformed values are removed
    # BEFORE summaries, highlights, Record of the Day, archive JSON,
    # latest.json, and weekly recap.
    records, quarantined = validate_records(candidates, target)
    records.sort(key=lambda r: (r["province"], r["community"], r["type"]))

    invalid_count = sum(item["category"] == "invalid" for item in quarantined)
    review_count = sum(item["category"] == "review" for item in quarantined)

    record_of_day = choose_record_of_day(records, target)
    oldest_age = max(
        (target.year - int(r["previousYear"]) for r in records if r.get("previousYear")),
        default=0,
    )
    source_updates = [r["sourceUpdated"] for r in records if r.get("sourceUpdated")]

    notes = [
        "Values may be revised by ECCC after initial publication.",
        "The archive contains daily snapshots saved by RecordWatch Canada after launch.",
        "Suspicious or malformed candidate records are automatically withheld pending validation or verification.",
    ]
    if quarantined:
        notes.append(
            f"{len(quarantined)} candidate record(s) were withheld by automated validation for this climate date."
        )

    payload = {
        "schemaVersion": 1,
        "date": target.isoformat(),
        "latestAvailableDate": target.isoformat(),
        "generatedAt": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "sourceLastUpdated": max(source_updates, default="Not reported"),
        "source": "Environment and Climate Change Canada — MSC GeoMet LTCE",
        "isDemo": False,
        "summary": {
            "totalRecords": len(records),
            "communities": len({r['community'] for r in records}),
            "tiedRecords": sum(r['status'] == 'tied' for r in records),
            "oldestRecordAge": oldest_age,
        },
        "validation": {
            "candidateRecords": len(candidates),
            "publishedRecords": len(records),
            "withheldRecords": len(quarantined),
            "invalidRecords": invalid_count,
            "manualReviewRecords": review_count,
        },
        "recordOfDay": record_of_day,
        "story": build_story(record_of_day),
        "highlights": build_highlights(records),
        "records": records,
        "notes": notes,
    }

    archive_path = (
        DATA_DIR / "archive" / f"{target.year:04d}" / f"{target.month:02d}" /
        f"{target.isoformat()}.json"
    )
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

    print(f"Wrote {len(records)} published records to {archive_path.relative_to(ROOT)}")
    if quarantined:
        print(
            f"Withheld {len(quarantined)} candidate record(s): "
            f"{invalid_count} invalid, {review_count} manual review."
        )
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
