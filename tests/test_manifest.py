from collections.abc import MutableMapping
from datetime import date, timedelta
from typing import cast

import pytest

from custom_components.resiyhome_health_sync.const import (
    DOMAIN,
    MANUAL_REFRESH_COOLDOWN,
    SCAN_INTERVAL,
    SCOPES,
)
from custom_components.resiyhome_health_sync.models import DailySummary, SourceKind


def test_immutable_contracts() -> None:
    assert DOMAIN == "resiyhome_health_sync"
    assert SCAN_INTERVAL == timedelta(minutes=15)
    assert MANUAL_REFRESH_COOLDOWN == timedelta(minutes=5)
    assert SCOPES == (
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    )
    assert set(SourceKind) == {
        SourceKind.FITBIT,
        SourceKind.APPLE_FALLBACK,
        SourceKind.MIXED,
        SourceKind.UNAVAILABLE,
    }


def test_daily_summary_sleep_stages_cannot_be_mutated() -> None:
    summary = DailySummary(date=date(2042, 7, 13), sleep_stages={"deep": 120.0})
    sleep_stages = cast(MutableMapping[str, float], summary.sleep_stages)

    with pytest.raises(TypeError):
        sleep_stages["deep"] = 0.0
