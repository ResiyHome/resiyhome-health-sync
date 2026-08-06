"""Tests for sanitized Google Health paired-device normalization."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from custom_components.resiyhome_health_sync.paired_devices import (
    normalize_paired_devices,
)


def _paired_device(
    name: str = "users/me/pairedDevices/private-device-123",
    **overrides: object,
) -> dict[str, object]:
    return {
        "name": name,
        "deviceType": "TRACKER",
        "batteryStatus": "High",
        "batteryLevel": 84,
        "lastSyncTime": "2042-08-05T12:30:00Z",
        "deviceVersion": "Fitbit Charge 7",
        "macAddress": "AA:BB:CC:DD:EE:FF",
        "features": ["HEART_RATE", "GPS"],
        "serialNumber": "private-serial",
        **overrides,
    }


def test_normalizes_only_the_documented_sanitized_paired_device_fields() -> None:
    """Raw resource identity and private device metadata never survive normalization."""
    resource_name = "users/me/pairedDevices/private-device-123"
    raw_device = _paired_device(resource_name)

    devices = normalize_paired_devices([raw_device])

    assert len(devices) == 1
    assert asdict(devices[0]) == {
        "identity_digest": "577fa4f7736cb1d1aa4fb6e3b8c9ca28",
        "device_type": "TRACKER",
        "product_name": "Fitbit Charge 7",
        "battery_status": "High",
        "battery_percentage": 84,
        "last_sync": datetime(2042, 8, 5, 12, 30, tzinfo=UTC),
    }
    retained = repr(devices)
    for private_value in (
        resource_name,
        "AA:BB:CC:DD:EE:FF",
        "HEART_RATE",
        "GPS",
        "private-serial",
    ):
        assert private_value not in retained


def test_identical_duplicates_are_removed_and_sorted_by_identity_digest() -> None:
    """Pagination order cannot duplicate or reorder normalized device identity."""
    device_alpha = _paired_device("users/me/pairedDevices/device-alpha")
    device_zeta = _paired_device(
        "users/me/pairedDevices/device-zeta",
        deviceType="SCALE",
        deviceVersion="Fitbit Aria",
        batteryStatus="Medium",
        batteryLevel=60,
    )

    devices = normalize_paired_devices([device_alpha, device_zeta, device_zeta.copy()])

    assert tuple(device.identity_digest for device in devices) == (
        "2d6aea0e8012d8241de70951fff4e793",
        "f6468325ef41a7060c6ec42f7c5287bd",
    )


def test_conflicting_duplicate_resource_is_rejected() -> None:
    """One Google identity cannot silently produce two different sanitized records."""
    original = _paired_device()
    conflicting = _paired_device(batteryLevel=83)

    with pytest.raises(ValueError):
        normalize_paired_devices([original, conflicting])


@pytest.mark.parametrize("identity", [None, "", "   ", "users/me/pairedDevices/"])
def test_missing_or_incomplete_resource_identity_is_rejected(identity: object) -> None:
    """A stable digest cannot be created from a missing Google resource identity."""
    payload = _paired_device()
    payload["name"] = identity

    with pytest.raises(ValueError):
        normalize_paired_devices([payload])


def test_absent_resource_identity_is_rejected() -> None:
    """Omitting the resource name fails closed instead of creating a shared identity."""
    payload = _paired_device()
    payload.pop("name")

    with pytest.raises(ValueError):
        normalize_paired_devices([payload])


@pytest.mark.parametrize("battery_level", [-1, 101, 84.0, True, "84"])
def test_invalid_battery_level_is_rejected(battery_level: object) -> None:
    """Only integer battery percentages in Google's 0 through 100 range are retained."""
    with pytest.raises(ValueError):
        normalize_paired_devices([_paired_device(batteryLevel=battery_level)])


@pytest.mark.parametrize(
    "battery_status",
    ["", "HIGH", "BATTERY_STATUS_OK", "Charging", 1],
)
def test_invalid_battery_status_is_rejected(battery_status: object) -> None:
    """Only Google's documented High, Medium, Low, and Empty status values are accepted."""
    with pytest.raises(ValueError):
        normalize_paired_devices([_paired_device(batteryStatus=battery_status)])


@pytest.mark.parametrize(
    "timestamp",
    [
        "2042-08-05T12:30:00",
        "2042-08-05 12:30:00Z",
        "2042-08-05T12:30:00+2500",
        "not-a-timestamp",
        123,
    ],
)
def test_invalid_or_naive_last_sync_timestamp_is_rejected(timestamp: object) -> None:
    """Last sync accepts strict timezone-aware RFC 3339 and rejects ambiguous values."""
    with pytest.raises(ValueError):
        normalize_paired_devices([_paired_device(lastSyncTime=timestamp)])


def test_boundary_year_offset_overflow_is_rejected_as_generic_value_error() -> None:
    """A valid-looking offset that underflows UTC cannot escape as OverflowError."""
    with pytest.raises(
        ValueError,
        match="paired device contains an invalid last sync timestamp",
    ):
        normalize_paired_devices(
            [_paired_device(lastSyncTime="0001-01-01T00:00:00+14:00")]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deviceType", "WATCH"),
        ("deviceType", ""),
        ("deviceVersion", ""),
        ("deviceVersion", 7),
    ],
)
def test_invalid_documented_device_fields_are_rejected(field: str, value: object) -> None:
    """Malformed type and product values never enter the normalized snapshot."""
    with pytest.raises(ValueError):
        normalize_paired_devices([_paired_device(**{field: value})])


def test_optional_battery_and_last_sync_fields_may_be_absent() -> None:
    """A paired scale without current battery or sync metadata remains discoverable."""
    payload = _paired_device(deviceType="SCALE", deviceVersion="Fitbit Aria")
    payload.pop("batteryStatus")
    payload.pop("batteryLevel")
    payload.pop("lastSyncTime")

    (device,) = normalize_paired_devices([payload])

    assert device.battery_status is None
    assert device.battery_percentage is None
    assert device.last_sync is None
