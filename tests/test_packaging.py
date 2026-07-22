"""Packaging and documentation checks for Health Sync."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from custom_components.resiyhome_health_sync.const import SCOPES
from scripts.verify_public_release import scan_text

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = {
    ("docs", "superpowers"),
    ("docs", "verification"),
}
TASK_6_PUBLIC_ALLOWLIST = (
    ".github",
    ".gitignore",
    "assets",
    "custom_components/resiyhome_health_sync",
    "tests",
    "scripts/__init__.py",
    "scripts/health_sync_probe.py",
    "scripts/verify_public_release.py",
    "README.md",
    "LICENSE",
    "TRADEMARKS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "hacs.json",
    "pyproject.toml",
    "docs/installation.md",
    "docs/google-cloud-oauth.md",
    "docs/multi-user.md",
    "docs/entities.md",
    "docs/actions-and-history.md",
    "docs/data-and-privacy.md",
    "docs/troubleshooting.md",
    "docs/upgrading-and-removal.md",
)
_CANDIDATE_TEST_ENV = "RESIYHOME_TASK_6_CANDIDATE_TEST"
_PUBLIC_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "*.pyc",
    "*.pyo",
)


def _tracked_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(ROOT / path for path in result.stdout.split("\0") if path)


def _tracked_public_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in _tracked_paths()
        if path.relative_to(ROOT).parts[:2] not in PRIVATE_PREFIXES
    )


def _build_task_6_candidate(destination: Path) -> None:
    for relative in TASK_6_PUBLIC_ALLOWLIST:
        source = ROOT / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, ignore=_PUBLIC_COPY_IGNORE)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _initialize_public_repository(candidate: Path) -> None:
    commands = (
        ("git", "init", "-b", "main"),
        ("git", "config", "user.name", "ResiyHome Release Tests"),
        ("git", "config", "user.email", "release-tests" + "@example.invalid"),
        ("git", "add", "."),
        ("git", "commit", "-m", "test: build Task 6 public candidate"),
    )
    for command in commands:
        subprocess.run(command, cwd=candidate, check=True, capture_output=True, text=True)


def test_hacs_metadata_marks_custom_integration() -> None:
    metadata = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert metadata["name"] == "Health Sync by ResiyHome"
    assert metadata["content_in_root"] is False
    assert metadata["render_readme"] is True
    assert metadata["domains"] == ["resiyhome_health_sync"]


def test_readme_documents_only_required_read_only_scopes() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for scope in SCOPES:
        assert scope in readme
    assert "write" not in readme.lower()
    assert "writeonly" not in readme.lower()
    assert "Energy dashboard remains outside this integration" in readme


def test_readme_uses_the_v1_public_release_identity() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("![Health Sync by ResiyHome](assets/health-sync-by-resiyhome.png)\n\n")
    assert readme.index("assets/health-sync-by-resiyhome.png") < readme.index(
        "# Health Sync by ResiyHome"
    )
    assert "Release 1.0.0" in readme
    assert "0.3.0" not in readme


def test_readme_documents_expanded_metrics_release_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for documented_text in (
        "## Expanded Metrics",
        "Enabled by default",
        "Active-zone minutes",
        "Daily VO2 max",
        "Daily oxygen saturation",
        "Daily respiratory rate",
        "Sleep respiratory rate",
        "Floors today",
        "Sedentary minutes today",
        "Heart-rate-zone minutes",
        "Disabled by default",
        "active-zone minutes for fat-burn, cardio, and peak zones",
        "heart-rate-zone minutes for light, moderate, vigorous, and peak zones",
        "calories for light, moderate, vigorous, and peak heart-rate zones",
        "sleep respiratory rate for deep, light, and REM sleep",
        "Weight",
        "include_body_measurements",
        "90-day normalized backfill",
        "reconciled daily summaries and daily rollups",
        "unavailable",
        "valid zero",
        "raw API payloads",
        "Google identifiers",
        "## Installation and upgrade",
    ):
        assert documented_text in readme


def test_readme_documents_reconciliation_and_exact_poll_request_counts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for documented_text in (
        "Google-reconciled all-source stream",
        "fully successful, non-paginated refresh",
        "31 logical data requests",
        "32 when body measurements are enabled",
        "one-time authentication retry",
        "Pagination can increase the actual HTTP request count",
        "Authentication failure stops the remaining poll immediately",
        "individual metric failures are isolated",
        "Only expanded-metric polling avoids raw high-volume streams",
    ):
        assert documented_text in readme


def test_private_prefix_contract_is_exact_and_excluded_from_public_scans() -> None:
    public_paths = _tracked_public_paths()

    assert PRIVATE_PREFIXES == {
        ("docs", "superpowers"),
        ("docs", "verification"),
    }
    assert not any(
        path.relative_to(ROOT).parts[:2] in PRIVATE_PREFIXES for path in public_paths
    )
    assert not (ROOT / "scripts" / "pilot_report.py").exists()
    assert not (ROOT / "tests" / "test_pilot_report.py").exists()


@pytest.mark.skipif(
    os.environ.get(_CANDIDATE_TEST_ENV) == "1",
    reason="the outer packaging test already created the exact Task 6 candidate",
)
def test_task_6_public_candidate_passes_without_private_docs(tmp_path: Path) -> None:
    candidate = tmp_path / "public-candidate"
    _build_task_6_candidate(candidate)

    excluded_cache_parts = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    assert not any(
        excluded_cache_parts.intersection(path.relative_to(candidate).parts)
        for path in candidate.rglob("*")
    )
    _initialize_public_repository(candidate)

    assert not (candidate / "docs" / "superpowers").exists()
    assert not (candidate / "docs" / "verification").exists()

    environment = os.environ.copy()
    environment[_CANDIDATE_TEST_ENV] = "1"
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=candidate,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr

    scanner = subprocess.run(
        [sys.executable, "scripts/verify_public_release.py", "."],
        cwd=candidate,
        capture_output=True,
        text=True,
    )
    assert scanner.returncode == 0, scanner.stdout + scanner.stderr


def test_tracked_public_text_is_anonymized() -> None:
    case_insensitive_markers = (
        "".join(("ru", "ss")),
        "".join(("jai", "me")),
        "".join(("ave", "ry")),
        "".join(("sher", "wood", "-home")),
        "/" + "config/custom_components",
    )
    case_sensitive_markers = ("/" + "Users/",)
    email_pattern = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

    for path in _tracked_public_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for marker in case_insensitive_markers:
            assert marker.lower() not in lowered, f"{marker!r} found in {path.relative_to(ROOT)}"
        for marker in case_sensitive_markers:
            assert marker not in text, f"{marker!r} found in {path.relative_to(ROOT)}"
        assert email_pattern.search(text) is None, f"email found in {path.relative_to(ROOT)}"


def test_manifest_preserves_required_public_codeowner() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "resiyhome_health_sync" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["codeowners"] == ["@" + "rwo" + "lfe5420"]


def test_tracked_public_relative_paths_pass_release_scanner() -> None:
    for path in _tracked_public_paths():
        relative_path = path.relative_to(ROOT)
        scan_text(relative_path.as_posix(), f"path name {relative_path.as_posix()}")


def test_python_ci_fetches_full_git_history() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "python scripts/verify_public_release.py ." in workflow


def test_changelog_documents_expanded_metrics_and_backfill_release() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = changelog.split("## 1.0.0", maxsplit=1)[1]

    assert "Expanded active-zone" in release
    assert "history backfill" in release
    assert "strict normalized history validation" in release


def test_fixture_provenance_is_documented() -> None:
    provenance = (ROOT / "tests" / "fixtures" / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    for required_text in (
        "All fixtures in this directory are synthetic test data",
        "generate_steps_fixture.py",
        "fixed fictional date in 2042",
        "no random input or external data source",
        "hand-authored synthetic contract examples",
        "not copied from an account, device, diagnostic, or live response",
    ):
        assert required_text in normalized


def test_test_data_uses_future_dates_except_documented_dst_boundaries() -> None:
    legacy_year = "".join(("20", "26"))
    serialized_date = re.compile(rf"{legacy_year}-(?P<month>\d{{2}})-")
    constructed_date = re.compile(
        rf"(?:date|datetime)\({legacy_year}, (?P<month>\d{{1,2}}),"
    )
    mapping_date = re.compile(
        rf"[\"']year[\"']\s*:\s*[\"']?{legacy_year}[\"']?\s*,\s*"
        r"[\"']month[\"']\s*:\s*(?P<month>\d{1,2})\s*,"
    )
    permitted_dst_files = {"test_api.py", "test_coordinator.py"}
    paths = (*sorted((ROOT / "tests").glob("*.py")), ROOT / "tests" / "fixtures")

    for path in paths:
        if path.is_dir():
            fixture_text = "\n".join(
                child.read_text(encoding="utf-8")
                for child in sorted(path.iterdir())
                if child.is_file()
            )
            texts = ((path, fixture_text),)
        else:
            texts = ((path, path.read_text(encoding="utf-8")),)

        for source, text in texts:
            for pattern in (serialized_date, constructed_date, mapping_date):
                for match in pattern.finditer(text):
                    assert source.name in permitted_dst_files
                    assert int(match.group("month")) in {3, 11}


def test_inline_payloads_exclude_record_and_device_identifier_fields() -> None:
    identifier_keys = (
        "".join(("external", "Id")),
        "".join(("health", "UserId")),
        "".join(("record", "Id")),
        "".join(("resource", "Name")),
        "".join(("source", "Id")),
        "".join(("user", "Id")),
        "".join(("web", "ClientId")),
        "".join(("device", "Id")),
        "".join(("device", "Identifier")),
    )
    assignment = re.compile(
        rf"[\"'](?:{'|'.join(re.escape(key) for key in identifier_keys)})[\"']\s*:"
    )

    for path in (*sorted((ROOT / "tests").glob("*.py")), ROOT / "tests" / "fixtures"):
        if path.is_dir():
            text = "\n".join(
                child.read_text(encoding="utf-8")
                for child in sorted(path.iterdir())
                if child.is_file()
            )
        else:
            text = path.read_text(encoding="utf-8")
        assert assignment.search(text) is None, path


def test_readme_documents_exactly_one_post_download_restart() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install_first = (
        "1. Install or update Health Sync in HACS and wait until the download "
        "is fully complete."
    )
    restart_lines = [
        line.strip() for line in readme.splitlines() if "restart home assistant" in line.lower()
    ]

    assert install_first in readme
    assert restart_lines == [
        "2. Do not restart Home Assistant earlier, even if HACS prompts you.",
        "3. After the install or update is fully downloaded, restart Home Assistant exactly once.",
    ]
    assert (
        readme.index(install_first)
        < readme.index(restart_lines[0])
        < readme.index(restart_lines[1])
    )


def test_manifest_versions_expanded_metrics_release() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "resiyhome_health_sync" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["version"] == "1.0.0"


def test_gitignore_blocks_credential_artifacts() -> None:
    ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    assert "client_secret*.json" in ignored
    assert "*token*.json" in ignored
    assert ".env" in ignored
    assert "*.credentials.json" in ignored
    assert ".pytest_cache/" in ignored
    assert ".mypy_cache/" in ignored
    assert ".ruff_cache/" in ignored
