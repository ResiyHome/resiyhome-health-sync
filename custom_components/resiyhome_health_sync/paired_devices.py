"""Sanitized normalization for Google Health paired devices."""

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from .models import PairedDeviceSummary

_RESOURCE_NAME_RE = re.compile(r"users/[^\s/]+/pairedDevices/[^\s/]+")
_RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})"
)
_DEVICE_TYPES = frozenset({"TRACKER", "SCALE"})
_BATTERY_STATUSES = frozenset({"High", "Medium", "Low", "Empty"})


def normalize_paired_devices(
    payloads: Iterable[Mapping[str, object]],
) -> tuple[PairedDeviceSummary, ...]:
    """Return normalized paired devices without retaining raw identifiers."""
    devices: dict[str, PairedDeviceSummary] = {}
    for payload in payloads:
        resource_name = _resource_name(payload)
        device = PairedDeviceSummary(
            identity_digest=hashlib.sha256(resource_name.encode("utf-8")).hexdigest()[:32],
            device_type=_device_type(payload),
            product_name=_required_text(payload, "deviceVersion"),
            battery_status=_battery_status(payload),
            battery_percentage=_battery_percentage(payload),
            last_sync=_last_sync(payload),
        )
        existing = devices.get(device.identity_digest)
        if existing is not None and existing != device:
            raise ValueError("conflicting paired-device resource")
        devices[device.identity_digest] = device
    return tuple(devices[digest] for digest in sorted(devices))


def _resource_name(payload: Mapping[str, object]) -> str:
    value = payload.get("name")
    if not isinstance(value, str) or _RESOURCE_NAME_RE.fullmatch(value) is None:
        raise ValueError("paired device is missing a valid resource identity")
    return value


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("paired device contains an invalid text field")
    return value.strip()


def _device_type(payload: Mapping[str, object]) -> str:
    value = payload.get("deviceType")
    if not isinstance(value, str) or value not in _DEVICE_TYPES:
        raise ValueError("paired device contains an invalid device type")
    return value


def _battery_status(payload: Mapping[str, object]) -> str | None:
    if "batteryStatus" not in payload:
        return None
    value = payload["batteryStatus"]
    if not isinstance(value, str) or value not in _BATTERY_STATUSES:
        raise ValueError("paired device contains an invalid battery status")
    return value


def _battery_percentage(payload: Mapping[str, object]) -> int | None:
    if "batteryLevel" not in payload:
        return None
    value = payload["batteryLevel"]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("paired device contains an invalid battery level")
    return value


def _last_sync(payload: Mapping[str, object]) -> datetime | None:
    if "lastSyncTime" not in payload:
        return None
    value = payload["lastSyncTime"]
    if not isinstance(value, str) or _RFC3339_RE.fullmatch(value) is None:
        raise ValueError("paired device contains an invalid last sync timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        normalized = parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        normalized = None
    if normalized is None:
        raise ValueError("paired device contains an invalid last sync timestamp")
    return normalized
