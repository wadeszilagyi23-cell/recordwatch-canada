#!/usr/bin/env python3
"""RecordWatch Canada V1.2 validation regression self-test.

This script exercises the publication validation gate with synthetic records only.
It does not call ECCC, and it does not write latest.json, archive files, or any
other public website data.
"""

from __future__ import annotations

import contextlib
import copy
import io
from datetime import date

import update_records as rw

TARGET = date(2026, 8, 10)


def make_record(record_id, record_type, value, previous, *, province="ON", coordinates=None, status=None, unit=None):
    if coordinates is None:
        coordinates = [-79.3832, 43.6532]
    if status is None:
        status = "tied" if abs(value - previous) <= 0.049 else "broken"
    if unit is None:
        unit = {
            "high_max": "°C", "high_min": "°C", "low_max": "°C", "low_min": "°C",
            "precipitation": "mm", "snowfall": "cm",
        }[record_type]
    begin_year = 1950
    return {
        "id": record_id,
        "date": TARGET.isoformat(),
        "community": "Validation Test",
        "province": province,
        "provinceName": rw.PROVINCE_NAMES.get(province, province),
        "type": record_type,
        "status": status,
        "value": value,
        "unit": unit,
        "previousValue": previous,
        "previousYear": 2020,
        "difference": round(value - previous, 2),
        "recordBeginYear": begin_year,
        "periodYears": TARGET.year - begin_year + 1,
        "coordinates": coordinates,
        "sourceUpdated": "2026-08-10T12:00:00Z",
        "sourceId": f"synthetic-{record_id}",
    }


def run_gate(record, *, approved_ids=None, rejected_ids=None):
    approved_ids = approved_ids or set()
    rejected_ids = rejected_ids or set()
    original_loader = rw.load_validation_overrides
    rw.load_validation_overrides = lambda: (set(approved_ids), set(rejected_ids))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            accepted, quarantined = rw.validate_records([copy.deepcopy(record)], TARGET)
    finally:
        rw.load_validation_overrides = original_loader
    return accepted, quarantined


def expect(label, record, expected, *, approved_ids=None, rejected_ids=None):
    accepted, quarantined = run_gate(
        record,
        approved_ids=approved_ids,
        rejected_ids=rejected_ids,
    )
    if expected == "accepted":
        passed = len(accepted) == 1 and len(quarantined) == 0
        actual = "accepted" if passed else (quarantined[0]["category"] if quarantined else "unexpected")
    else:
        passed = len(accepted) == 0 and len(quarantined) == 1 and quarantined[0].get("category") == expected
        actual = quarantined[0].get("category") if quarantined else "accepted"
    if passed:
        print(f"PASS  {label}: {expected}")
        return True
    reason = quarantined[0].get("reason") if quarantined else "none"
    print(f"FAIL  {label}: expected {expected}, got {actual}; reason={reason}")
    return False


def main():
    tests = []
    tests.append(expect("ordinary legitimate temperature", make_record("test-normal-temp", "high_max", 32.0, 31.0), "accepted"))
    tests.append(expect("250 mm precipitation", make_record("test-precip-250", "precipitation", 250.0, 60.7), "review"))
    tests.append(expect("55 C temperature", make_record("test-temp-55", "high_max", 55.0, 49.0), "review"))
    tests.append(expect("200 cm snowfall", make_record("test-snow-200", "snowfall", 200.0, 80.0), "review"))
    tests.append(expect("invalid province code", make_record("test-bad-province", "high_max", 32.0, 31.0, province="ZZ"), "invalid"))
    tests.append(expect("invalid coordinates", make_record("test-bad-coordinates", "high_max", 32.0, 31.0, coordinates=[-200.0, 95.0]), "invalid"))
    tests.append(expect("wrong record direction", make_record("test-wrong-direction", "high_max", 20.0, 30.0, status="broken"), "invalid"))

    approved_outlier = make_record("test-approved-outlier", "precipitation", 175.0, 100.0)
    tests.append(expect("verified outlier override", approved_outlier, "accepted", approved_ids={approved_outlier["id"]}))

    approved_bad_structure = make_record("test-approved-bad-structure", "high_max", 32.0, 31.0, province="ZZ")
    tests.append(expect("approval cannot bypass structural validation", approved_bad_structure, "invalid", approved_ids={approved_bad_structure["id"]}))

    passed = sum(tests)
    total = len(tests)
    print()
    if passed == total:
        print(f"SUCCESS: {passed}/{total} validation self-tests passed.")
        print("The synthetic test did not modify RecordWatch website data.")
        return 0
    print(f"FAILURE: {passed}/{total} validation self-tests passed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
