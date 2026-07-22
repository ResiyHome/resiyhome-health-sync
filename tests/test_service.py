"""Tests for Health Sync refresh service registration and targeting."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.resiyhome_health_sync import (
    async_setup_entry,
    async_unload_entry,
    config_flow,
)
from custom_components.resiyhome_health_sync.const import DOMAIN, SCOPES
from custom_components.resiyhome_health_sync.models import (
    CoordinatorSnapshot,
    DailySummary,
    SourceKind,
)

NOW = datetime(2042, 7, 13, 12, 0, tzinfo=UTC)


def _entry(
    hass, *, person_name: str = "Sample Alpha", person_slug: str = "sample_alpha"
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=person_name,
        unique_id=person_slug,
        data={
            "person_name": person_name,
            "person_slug": person_slug,
            "client_id": f"{person_slug}-client-id",
            "client" + "_secret": f"{person_slug}-client-secret",
            "access" + "_token": f"{person_slug}-access-token",
            "refresh" + "_token": f"{person_slug}-refresh-token",
            "expires_at": "2042-07-13T13:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)
    return entry


def _register_integration(hass) -> None:
    module = MockModule(
        DOMAIN,
        async_setup_entry=async_setup_entry,
        async_unload_entry=async_unload_entry,
        partial_manifest={"config_flow": True, "version": "0.1.0"},
    )
    mock_integration(hass, module, built_in=False)
    mock_platform(hass, f"{DOMAIN}.config_flow", config_flow, built_in=False)


def _summary(day: date = date(2042, 7, 13), *, steps: int = 100) -> DailySummary:
    return DailySummary(
        date=day,
        steps=steps,
        fitbit_steps=steps - 10,
        source=SourceKind.FITBIT,
        complete=False,
        updated_at=NOW,
    )


def _runtime_patches(summary: DailySummary | None = None):
    client = MagicMock()
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[])
    history.async_query = AsyncMock(return_value=[])
    coordinator = MagicMock()
    coordinator.data = CoordinatorSnapshot(
        current_day=summary or _summary(),
        last_success=NOW,
        last_attempt=NOW,
        authorization_healthy=True,
        backfill_cursor=date(2042, 7, 1),
        backfill_complete=True,
    )
    coordinator.data_types = ("steps", "sleep")
    coordinator.is_stale = False
    coordinator.async_refresh_current = AsyncMock(return_value=coordinator.data)
    coordinator.async_manual_refresh = AsyncMock(return_value=coordinator.data)
    coordinator.async_probe_optional_data_types = AsyncMock(return_value={})
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_backfill_step = AsyncMock()
    return client, history, coordinator


async def _setup_entry(hass, entry: MockConfigEntry, history, coordinator):
    _register_integration(hass)
    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch("custom_components.resiyhome_health_sync.HealthHistoryStore", return_value=history),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            return_value=coordinator,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_refresh_service_is_registered_once_for_loaded_entries(hass) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    sample_beta = _entry(hass, person_name="Sample Beta", person_slug="sample_beta")
    _client, _history, sample_alpha_coordinator = _runtime_patches(_summary(steps=100))
    _client2, _history2, sample_beta_coordinator = _runtime_patches(_summary(steps=200))
    coordinators = [sample_alpha_coordinator, sample_beta_coordinator]
    _register_integration(hass)

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthHistoryStore",
            side_effect=[_history, _history2],
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=coordinators,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, sample_alpha)
        assert hass.services.has_service(DOMAIN, "refresh")
        assert await async_setup_entry(hass, sample_beta)
        assert hass.services.has_service(DOMAIN, "refresh")
        await hass.services.async_call(DOMAIN, "refresh", {"person": "sample_beta"}, blocking=True)

    sample_alpha_coordinator.async_manual_refresh.assert_not_awaited()
    sample_beta_coordinator.async_manual_refresh.assert_awaited_once_with()


async def test_refresh_service_rejects_missing_or_ambiguous_person(hass) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    _client, history, coordinator = _runtime_patches()
    await _setup_entry(hass, sample_alpha, history, coordinator)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "refresh", {"person": "person_three"}, blocking=True)

    coordinator.async_manual_refresh.assert_not_awaited()


async def test_refresh_service_removed_after_last_loaded_entry_unloads(hass) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    sample_beta = _entry(hass, person_name="Sample Beta", person_slug="sample_beta")
    _client, _history, sample_alpha_coordinator = _runtime_patches()
    _client2, _history2, sample_beta_coordinator = _runtime_patches()
    _register_integration(hass)

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthHistoryStore",
            side_effect=[_history, _history2],
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=[sample_alpha_coordinator, sample_beta_coordinator],
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await async_setup_entry(hass, sample_alpha)
        assert await async_setup_entry(hass, sample_beta)
        assert hass.services.has_service(DOMAIN, "refresh")
        assert hass.services.has_service(DOMAIN, "probe_optional_data_types")

        assert await async_unload_entry(hass, sample_alpha)
        assert hass.services.has_service(DOMAIN, "refresh")
        assert hass.services.has_service(DOMAIN, "probe_optional_data_types")

        assert await async_unload_entry(hass, sample_beta)
        assert not hass.services.has_service(DOMAIN, "refresh")
        assert not hass.services.has_service(DOMAIN, "probe_optional_data_types")


async def test_optional_probe_service_returns_redacted_result_for_one_person(hass) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    _client, history, coordinator = _runtime_patches()
    coordinator.async_probe_optional_data_types.return_value = {
        "active-zone-minutes": {
            "status": "ok",
            "raw_count": 2,
            "all_sources_count": 1,
            "wearables_count": 1,
            "source_platforms": ("FITBIT",),
        }
    }
    await _setup_entry(hass, sample_alpha, history, coordinator)

    response = await hass.services.async_call(
        DOMAIN,
        "probe_optional_data_types",
        {"person": "sample_alpha", "days": 7},
        blocking=True,
        return_response=True,
    )

    coordinator.async_probe_optional_data_types.assert_awaited_once_with(days=7)
    assert response == {
        "person": "sample_alpha",
        "days": 7,
        "data_types": coordinator.async_probe_optional_data_types.return_value,
    }
    assert "token" not in str(response).lower()
    assert "payload" not in str(response).lower()


@pytest.mark.parametrize("days", [0, 15, True, "7"])
async def test_optional_probe_service_rejects_invalid_days(hass, days: object) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    _client, history, coordinator = _runtime_patches()
    await _setup_entry(hass, sample_alpha, history, coordinator)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "probe_optional_data_types",
            {"person": "sample_alpha", "days": days},
            blocking=True,
            return_response=True,
        )

    coordinator.async_probe_optional_data_types.assert_not_awaited()


async def test_optional_probe_service_removed_after_last_entry_unloads(hass) -> None:
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    _client, history, coordinator = _runtime_patches()
    await _setup_entry(hass, sample_alpha, history, coordinator)

    assert hass.services.has_service(DOMAIN, "probe_optional_data_types")
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, sample_alpha)
    assert not hass.services.has_service(DOMAIN, "probe_optional_data_types")
