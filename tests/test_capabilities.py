import pytest

from custom_components.resiyhome_health_sync.capabilities import (
    CapabilityId,
    UnsupportedScopeError,
    enabled_capabilities,
    requested_scopes,
    validate_granted_scopes,
)
from custom_components.resiyhome_health_sync.const import (
    BASE_SCOPES,
    NUTRITION_SCOPE,
    SETTINGS_SCOPE,
)


def test_existing_options_keep_only_baseline_capabilities() -> None:
    assert enabled_capabilities({}) == {
        CapabilityId.CORE_ACTIVITY,
        CapabilityId.SLEEP,
    }
    assert requested_scopes({}) == BASE_SCOPES


def test_body_measurements_use_existing_baseline_scope() -> None:
    options = {"include_body_measurements": True}

    assert CapabilityId.BODY_MEASUREMENTS in enabled_capabilities(options)
    assert requested_scopes(options) == BASE_SCOPES


def test_optional_options_add_only_their_readonly_scopes() -> None:
    options = {"include_nutrition": True, "include_paired_devices": True}

    assert requested_scopes(options) == (
        *BASE_SCOPES,
        NUTRITION_SCOPE,
        SETTINGS_SCOPE,
    )


def test_baseline_token_remains_valid() -> None:
    grant = validate_granted_scopes(BASE_SCOPES, {})

    assert grant.baseline_valid is True
    assert grant.available_capabilities == {
        CapabilityId.CORE_ACTIVITY,
        CapabilityId.SLEEP,
    }


def test_declined_optional_scope_preserves_baseline() -> None:
    grant = validate_granted_scopes(BASE_SCOPES, {"include_nutrition": True})

    assert grant.baseline_valid is True
    assert CapabilityId.NUTRITION not in grant.available_capabilities
    assert grant.missing_optional_scopes == frozenset({NUTRITION_SCOPE})


@pytest.mark.parametrize(
    "scope",
    [
        "https://www.googleapis.com/auth/googlehealth.nutrition",
        "https://www.googleapis.com/auth/drive.readonly",
    ],
)
def test_unknown_or_write_scope_is_rejected(scope: str) -> None:
    with pytest.raises(UnsupportedScopeError):
        validate_granted_scopes((*BASE_SCOPES, scope), {})
