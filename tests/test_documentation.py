from pathlib import Path

from custom_components.resiyhome_health_sync.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.resiyhome_health_sync.const import SCOPES
from custom_components.resiyhome_health_sync.sensor import SENSOR_DESCRIPTIONS

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


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_google_oauth_guide_is_complete() -> None:
    guide = _read("docs/google-cloud-oauth.md")
    for scope in SCOPES:
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


def test_privacy_boundaries_are_explicit() -> None:
    privacy = _read("docs/data-and-privacy.md")
    for phrase in (
        "does not connect directly to Apple Health",
        "normalized daily summaries",
        "raw Google API payloads",
        "OAuth credentials",
        "medical advice",
        "recursively redacted",
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

    assert "The person slug anchors entity unique IDs and service targeting." in guide
    assert (
        "The Home Assistant config entry ID independently isolates that person's "
        "normalized history store."
    ) in guide
    assert "It anchors entity unique IDs and the local history store." not in guide


def test_websocket_table_does_not_call_optional_metrics_required() -> None:
    guide = _read("docs/actions-and-history.md")

    assert "## Request fields" in guide
    assert "Required request fields" not in guide
