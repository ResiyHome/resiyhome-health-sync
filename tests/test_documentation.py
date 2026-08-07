from pathlib import Path

from custom_components.resiyhome_health_sync.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.resiyhome_health_sync.const import (
    NUTRITION_SCOPE,
    SCOPES,
    SETTINGS_SCOPE,
)
from custom_components.resiyhome_health_sync.sensor import (
    PAIRED_DEVICE_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
)

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_DOCUMENTS = (
    "README.md",
    "docs/installation.md",
    "docs/google-cloud-oauth.md",
    "docs/multi-user.md",
    "docs/entities.md",
    "docs/actions-and-history.md",
    "docs/data-and-privacy.md",
    "docs/troubleshooting.md",
    "docs/upgrading-and-removal.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
)
USER_GUIDES = (
    "README.md",
    "docs/installation.md",
    "docs/google-cloud-oauth.md",
    "docs/multi-user.md",
    "docs/entities.md",
    "docs/actions-and-history.md",
    "docs/data-and-privacy.md",
    "docs/troubleshooting.md",
    "docs/upgrading-and-removal.md",
    "CHANGELOG.md",
)
PARITY_STATIC_ENTITY_KEYS = (
    "total_calories_burned_today",
    "sleep_time_in_bed",
    "sleep_time_to_fall_asleep",
    "sleep_time_after_waking",
    "body_fat",
    "height",
    "calories_consumed_today",
    "water_consumed_today",
)
_FORBIDDEN_SLUG_IDENTITY_STATEMENTS = frozenset(
    {
        "enter a person slug",
        "enter the person slug",
        "enter your person slug",
        "choose a person slug",
        "choose the person slug",
        "choose your person slug",
        "choose a stable unique person slug",
        "provide a person slug",
        "provide the person slug",
        "provide your person slug",
        "history is keyed by a person slug",
        "history is keyed by the person slug",
        "normalized history is keyed by the person slug",
        "the normalized history store is keyed by the person slug",
        "person slug keys history",
        "the person slug keys history",
        "person slug owns history",
        "the person slug owns history",
        "the slug anchors entity identity and retained history",
        "reauthentication preserves the person slug, entity unique ids, and normalized history",
        (
            "reauthentication preserves the stable person slug, entity identity, "
            "and normalized history"
        ),
    }
)
_SENTENCE_BREAK_TRANSLATION = str.maketrans({".": "\n", "!": "\n", "?": "\n"})


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _slug_identity_violations(documentation: str) -> tuple[str, ...]:
    """Return exact forbidden complete sentences after case/whitespace normalization."""
    normalized = " ".join(documentation.casefold().split())
    statements = (
        statement.strip(" `*_#>-:;,'\"()[]")
        for statement in normalized.translate(_SENTENCE_BREAK_TRANSLATION).splitlines()
    )
    return tuple(
        statement
        for statement in statements
        if statement in _FORBIDDEN_SLUG_IDENTITY_STATEMENTS
    )


def test_google_oauth_guide_is_complete() -> None:
    guide = _read("docs/google-cloud-oauth.md")
    for scope in (*SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE):
        assert scope in guide
    for phrase in (
        "Enable the Google Health API",
        "Web application",
        "Authorized redirect URIs",
        "Application Credentials",
        "Testing",
        "In Production",
        "seven days",
        "client secret",
        "https://developers.google.com/health/setup",
        "https://health.googleapis.com/v4",
        "https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/reconcile",
        "https://developers.google.com/health/reference/rest/v4/users.pairedDevices/list",
        "include_nutrition",
        "include_paired_devices",
        "partial consent",
    ):
        assert phrase in guide


def test_multi_user_guide_is_explicit() -> None:
    guide = _read("docs/multi-user.md")
    for phrase in (
        "one Google Cloud project",
        "one config entry per person",
        "private browser window",
        "person slug",
        "Reauthenticate",
    ):
        assert phrase in guide


def test_entity_catalog_covers_every_key() -> None:
    catalog = _read("docs/entities.md")
    descriptions = (*SENSOR_DESCRIPTIONS, *BINARY_SENSOR_DESCRIPTIONS)
    for description in descriptions:
        assert f"`{description.key}`" in catalog


def test_entity_catalog_covers_parity_and_dynamic_paired_entities() -> None:
    catalog = _read("docs/entities.md")

    for key in PARITY_STATIC_ENTITY_KEYS:
        assert f"`{key}`" in catalog
    for description in PAIRED_DEVICE_SENSOR_DESCRIPTIONS:
        assert f"`{description.key}`" in catalog
    for phrase in (
        "eight static person entity keys",
        "Dynamic paired-device entities",
        "one battery and one last-sync entity per paired device",
        "Health Sync API refresh time",
        "Fitbit mobile-device sync time",
    ):
        assert phrase in catalog


def test_optional_capability_documentation_is_complete() -> None:
    documentation = "\n".join(_read(path) for path in USER_GUIDES)

    for text in (
        "include_nutrition",
        "include_paired_devices",
        "googlehealth.nutrition.readonly",
        "googlehealth.settings.readonly",
        "Total calories burned today",
        "Calories consumed today",
        "Water consumed today",
        "Body-fat percentage",
        "Paired-device last sync",
    ):
        assert text in documentation
    assert "Nutrition has no historical backfill in this release." in documentation


def test_existing_user_optional_capability_flow_is_explicit() -> None:
    guide = " ".join(_read("docs/upgrading-and-removal.md").split())

    for phrase in (
        "Do not delete or re-add the integration.",
        "Baseline sensors continue without reauthorization.",
        "Open that person's Health Sync options.",
        "Enable `include_nutrition`, `include_paired_devices`, or both.",
        "Complete Google reauthorization for that same person.",
        "Declining an optional permission leaves baseline sensors working.",
        "Repeat these steps separately for each household member.",
    ):
        assert phrase in guide


def test_body_measurement_entity_registry_defaults_are_complete() -> None:
    catalog = " ".join(_read("docs/entities.md").split())

    assert (
        "Weight, Body-fat percentage, and Height are created disabled by default "
        "in the entity registry."
    ) in catalog
    assert (
        "Enable each body-measurement entity in the entity registry before using it."
    ) in catalog


def test_privacy_boundaries_are_explicit() -> None:
    privacy = _read("docs/data-and-privacy.md")
    for phrase in (
        "does not connect directly to Apple Health",
        "normalized daily summaries",
        "raw Google API payloads",
        "OAuth credentials",
        "medical advice",
        "recursively redacted",
        "food names",
        "raw nutrition logs",
        "MAC addresses",
        "raw device resource IDs",
        "feature lists",
        "Daily normalized nutrition starts with the first successful opt-in refresh.",
        "Paired devices are current metadata and are never written to normalized history.",
        "Disabling `include_nutrition` stops future nutrition requests",
        "does not erase already normalized nutrition values",
        "Home Assistant Recorder history",
        "explicit operator action",
        "complete erasure cannot be guaranteed",
    ):
        assert phrase in privacy


def test_public_documentation_has_no_former_project_identity() -> None:
    for path in PUBLIC_DOCUMENTS:
        assert "".join(("sher", "wood")) not in _read(path).lower(), path


def test_issue_forms_repeat_the_sensitive_data_warning() -> None:
    warning = (
        "Do not attach OAuth credentials, client secrets, access or refresh tokens, "
        "raw Google Health responses, screenshots containing personal health values, "
        "or unredacted Home Assistant storage."
    )
    for path in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
    ):
        assert warning in " ".join(_read(path).split())


def test_removal_guide_does_not_instruct_direct_storage_edits() -> None:
    guide = _read("docs/upgrading-and-removal.md")

    assert ".storage" not in guide
    assert "does not currently provide a supported normalized-store erasure path" in guide
    assert "request support" in guide
    for boundary in (
        "config-entry removal",
        "Recorder data",
        "normalized integration storage",
        "OAuth revocation",
        "HACS files",
        "backups",
    ):
        assert boundary in guide


def test_actions_guide_explains_reliable_slug_discovery() -> None:
    guide = _read("docs/actions-and-history.md")

    assert "shown in that person's config entry" not in guide
    assert "stable unique ID" in guide
    assert "prefix before a known key such as `_steps_today`" in guide
    assert "manually renamed entity ID" in guide


def test_entity_catalog_clarifies_weight_registry_default() -> None:
    catalog = _read("docs/entities.md")

    assert "disabled by default in the entity registry" in catalog
    assert "may remain unavailable until body measurements are opted in" in catalog


def test_readme_distinguishes_weight_disabled_from_unavailable() -> None:
    readme = " ".join(_read("README.md").split())

    assert "Weight is disabled by default in the entity registry" in readme
    assert (
        "After you enable the entity, its state may be unavailable until body "
        "measurements are opted in and Google supplies usable data."
    ) in readme


def test_troubleshooting_distinguishes_weight_disabled_from_unavailable() -> None:
    guide = " ".join(_read("docs/troubleshooting.md").split())

    assert "Weight is disabled by default in the entity registry" in guide
    assert (
        "After you enable the entity, its state may be unavailable until body "
        "measurements are opted in and Google supplies usable data."
    ) in guide


def test_troubleshooting_unconditionally_prohibits_direct_storage_edits() -> None:
    guide = " ".join(_read("docs/troubleshooting.md").split())

    assert "Never directly edit Home Assistant `.storage`." in guide
    assert "Restore a backup or use a supported recovery path instead." in guide
    assert "Do not edit Home Assistant storage while Home Assistant is running." not in guide


def test_multi_user_distinguishes_entity_identity_from_storage_isolation() -> None:
    guide = " ".join(_read("docs/multi-user.md").split())

    assert "Home Assistant asks for a person name" in guide
    assert "Health Sync derives the stable person slug from that name." in guide
    assert "Users do not enter or choose the slug directly." in guide
    assert (
        "The derived slug provides stable entity unique IDs and service and action "
        "targeting within that existing config entry."
    ) in guide
    assert (
        "The normalized history store is keyed by the Home Assistant config-entry ID, "
        "not by the person slug."
    ) in guide


def test_public_guides_forbid_direct_slug_input_and_slug_owned_history() -> None:
    for path in USER_GUIDES:
        guide = _read(path)
        assert _slug_identity_violations(guide) == (), path


def test_slug_identity_guard_allows_explicit_negations() -> None:
    for correct_statement in (
        "Do not enter a person slug.",
        "The slug does not key history.",
    ):
        assert _slug_identity_violations(correct_statement) == (), correct_statement


def test_slug_identity_guard_rejects_precise_false_claims() -> None:
    for false_statement in (
        "History is keyed by the person slug.",
        "Person slug owns history.",
        "Enter a person slug.",
        "Choose a person slug.",
        "Provide a person slug.",
    ):
        assert _slug_identity_violations(false_statement) == (
            false_statement.casefold().removesuffix("."),
        )


def test_public_guide_guard_rejects_deliberately_mutated_guide() -> None:
    guide = _read("README.md")
    mutated_guide = f"{guide}\n\nHistory is keyed by the person slug.\n"

    assert _slug_identity_violations(guide) == ()
    assert _slug_identity_violations(mutated_guide) == (
        "history is keyed by the person slug",
    )


def test_public_guides_explain_config_entry_owned_history_lifecycle() -> None:
    documentation = " ".join(
        " ".join(_read(path).split()) for path in USER_GUIDES
    )

    for phrase in (
        "The setup UI asks for `person_name`, shown as Person name.",
        "Health Sync derives the stable person slug from that name.",
        "Users do not enter or choose the slug directly.",
        "The normalized history store is keyed by the Home Assistant config-entry ID, "
        "not by the person slug.",
        "Deleting and recreating the integration creates a new config-entry ID.",
        "Even when the same person name produces the same derived slug, the new entry "
        "does not reconnect the old normalized history store.",
    ):
        assert phrase in documentation


def test_websocket_table_does_not_call_optional_metrics_required() -> None:
    guide = _read("docs/actions-and-history.md")

    assert "## Request fields" in guide
    assert "Required request fields" not in guide


def test_history_guide_covers_parity_metrics_and_excludes_paired_metadata() -> None:
    guide = _read("docs/actions-and-history.md")

    for key in (
        "total_energy_kcal",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
        "nutrition_energy_kcal",
        "hydration_ml",
        "body_fat_percentage",
        "height_m",
    ):
        assert f"`{key}`" in guide
    for phrase in (
        "Nutrition has no historical backfill in this release.",
        "first successful opt-in refresh",
        "Paired-device battery and sync metadata are not supported history metrics",
        "current metadata only",
    ):
        assert phrase in guide


def test_docs_use_current_google_v4_paired_device_contract_names() -> None:
    documentation = "\n".join(_read(path) for path in USER_GUIDES)

    for current_contract in (
        "`deviceVersion`",
        "`batteryStatus`",
        "`batteryLevel`",
        "`lastSyncTime`",
        "`High`, `Medium`, `Low`, or `Empty`",
    ):
        assert current_contract in documentation
    for stale_contract in (
        "`sampleTime`",
        "`productName`",
        "`BATTERY_STATUS_HIGH`",
        "`BATTERY_STATUS_MEDIUM`",
        "`BATTERY_STATUS_LOW`",
        "`BATTERY_STATUS_EMPTY`",
    ):
        assert stale_contract not in documentation
