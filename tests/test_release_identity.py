import ast
from pathlib import Path

from custom_components.resiyhome_health_sync.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
from custom_components.resiyhome_health_sync.const import DOMAIN, SCOPES
from custom_components.resiyhome_health_sync.sensor import SENSOR_DESCRIPTIONS
from custom_components.resiyhome_health_sync.websocket import _COMMAND

ROOT = Path(__file__).resolve().parents[1]


def test_public_release_identity() -> None:
    assert DOMAIN == "resiyhome_health_sync"
    assert _COMMAND == "resiyhome_health_sync/history"


def test_read_only_scopes_are_unchanged() -> None:
    assert SCOPES == (
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    )


def test_stable_entity_keys_survive_rebrand() -> None:
    assert {description.key for description in SENSOR_DESCRIPTIONS} == {
        "active_energy_today",
        "active_zone_cardio_minutes_today",
        "active_zone_fat_burn_minutes_today",
        "active_zone_minutes_today",
        "active_zone_peak_minutes_today",
        "average_heart_rate",
        "backfill_cursor",
        "backfill_status",
        "body_fat",
        "calories_consumed_today",
        "current_source",
        "daily_oxygen_saturation",
        "daily_respiratory_rate",
        "daily_vo2_max",
        "distance_today",
        "exercise_minutes_today",
        "fitbit_steps_today",
        "floors_today",
        "heart_rate_variability",
        "heart_rate_zone_light_calories_today",
        "heart_rate_zone_light_minutes_today",
        "heart_rate_zone_minutes_today",
        "heart_rate_zone_moderate_calories_today",
        "heart_rate_zone_moderate_minutes_today",
        "heart_rate_zone_peak_calories_today",
        "heart_rate_zone_peak_minutes_today",
        "heart_rate_zone_vigorous_calories_today",
        "heart_rate_zone_vigorous_minutes_today",
        "height",
        "steps_today",
        "last_successful_synchronization",
        "last_sleep_duration",
        "last_workout_duration",
        "last_workout_type",
        "maximum_heart_rate",
        "minimum_heart_rate",
        "resting_heart_rate",
        "sedentary_minutes_today",
        "sleep_awake_duration",
        "sleep_deep_duration",
        "sleep_deep_respiratory_rate",
        "sleep_time_after_waking",
        "sleep_time_in_bed",
        "sleep_time_to_fall_asleep",
        "sleep_light_duration",
        "sleep_light_respiratory_rate",
        "sleep_rem_duration",
        "sleep_rem_respiratory_rate",
        "sleep_respiratory_rate",
        "total_calories_burned_today",
        "water_consumed_today",
        "weight",
    }


def test_binary_entity_keys_are_stable() -> None:
    assert {description.key for description in BINARY_SENSOR_DESCRIPTIONS} == {
        "health_data_stale",
        "health_authorization_problem",
    }


def test_runtime_platforms_use_only_resiyhome_as_manufacturer() -> None:
    for relative_path in (
        "custom_components/resiyhome_health_sync/sensor.py",
        "custom_components/resiyhome_health_sync/binary_sensor.py",
    ):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        manufacturers = [
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "manufacturer"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ]
        assert manufacturers and set(manufacturers) == {"ResiyHome"}, relative_path
