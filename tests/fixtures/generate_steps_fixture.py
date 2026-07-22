"""Generate the deterministic synthetic reconciliation fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).with_name("steps_fitbit_healthkit.json")
DAY = "2042-07-13"
PREVIOUS_DAY = "2042-07-12"
UTC_OFFSET = "-14400s"


def _interval(start: str, end: str) -> dict[str, str]:
    return {
        "startTime": start,
        "startUtcOffset": UTC_OFFSET,
        "endTime": end,
        "endUtcOffset": UTC_OFFSET,
    }


def _steps(count: int, start: str, end: str) -> dict[str, Any]:
    return {"steps": {"interval": _interval(start, end), "count": str(count)}}


def build_fixture() -> dict[str, Any]:
    """Build a fictional v4 payload from fixed contract-only values."""
    first_start = f"{DAY}T10:00:00Z"
    first_end = f"{DAY}T11:00:00Z"
    second_start = f"{DAY}T10:30:00Z"
    second_end = f"{DAY}T11:30:00Z"
    reconciled_interval = _interval(first_start, second_end)

    return {
        "day": DAY,
        "raw": {
            "steps": [
                {"dataSource": {"platform": " fitbit "}, **_steps(1111, first_start, first_end)},
                {
                    "dataSource": {"platform": "health_kit"},
                    **_steps(2222, second_start, second_end),
                },
            ]
        },
        "all_sources": {
            "steps": [{"steps": {"interval": reconciled_interval, "count": "3000"}}],
            "distance": [
                {"distance": {"interval": reconciled_interval, "millimeters": "1234000"}}
            ],
            "active-energy-burned": [
                {"activeEnergyBurned": {"interval": reconciled_interval, "kcal": 123.0}}
            ],
            "active-minutes": [
                {
                    "activeMinutes": {
                        "interval": reconciled_interval,
                        "activeMinutesByActivityLevel": [
                            {"activityLevel": "LIGHT", "activeMinutes": "11"},
                            {"activityLevel": "MODERATE", "activeMinutes": "22"},
                        ],
                    }
                }
            ],
            "heart-rate": [
                {
                    "heartRate": {
                        "sampleTime": {"physicalTime": first_start, "utcOffset": UTC_OFFSET},
                        "beatsPerMinute": "60",
                    }
                },
                {
                    "heartRate": {
                        "sampleTime": {"physicalTime": first_end, "utcOffset": UTC_OFFSET},
                        "beatsPerMinute": "90",
                    }
                },
            ],
            "heart-rate-variability": [
                {
                    "heartRateVariability": {
                        "sampleTime": {
                            "physicalTime": f"{DAY}T06:30:00Z",
                            "utcOffset": UTC_OFFSET,
                        },
                        "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 33.0,
                    }
                }
            ],
            "daily-resting-heart-rate": [
                {
                    "dailyRestingHeartRate": {
                        "date": {"year": 2042, "month": 7, "day": 13},
                        "beatsPerMinute": "55",
                    }
                }
            ],
            "daily-heart-rate-variability": [
                {
                    "dailyHeartRateVariability": {
                        "date": {"year": 2042, "month": 7, "day": 13},
                        "averageHeartRateVariabilityMilliseconds": 44.0,
                    }
                }
            ],
            "exercise": [
                {
                    "exercise": {
                        "interval": _interval(f"{DAY}T14:00:00Z", f"{DAY}T14:30:00Z"),
                        "exerciseType": "WALKING",
                        "displayName": "Synthetic contract walk",
                        "activeDuration": "1800s",
                        "metricsSummary": {"caloriesKcal": 111.0},
                    }
                }
            ],
            "sleep": [
                {
                    "sleep": {
                        "interval": _interval(
                            f"{PREVIOUS_DAY}T23:30:00Z", f"{DAY}T07:00:00Z"
                        ),
                        "summary": {
                            "minutesAsleep": "390",
                            "stagesSummary": [
                                {"type": "AWAKE", "minutes": "30"},
                                {"type": "LIGHT", "minutes": "210"},
                                {"type": "DEEP", "minutes": "90"},
                                {"type": "REM", "minutes": "90"},
                            ],
                        },
                    }
                }
            ],
        },
        "wearables": {"steps": [_steps(1111, first_start, first_end)]},
    }


def _serialized_fixture() -> str:
    return json.dumps(build_fixture(), indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = _serialized_fixture()
    if args.check:
        if FIXTURE_PATH.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"{FIXTURE_PATH.name} is not generated from this script")
        return
    FIXTURE_PATH.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
