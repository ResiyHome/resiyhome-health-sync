"""Tests for public release privacy and identity guards."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from PIL import ExifTags, Image, PngImagePlugin

from scripts import verify_public_release
from scripts.verify_public_release import (
    ALLOWED_PNG_PATHS,
    ReleaseViolation,
    scan_history,
    scan_text,
    scan_tree,
)

ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_KEYS = (
    "".join(("access", "_token")),
    "".join(("refresh", "_token")),
    "".join(("client", "_secret")),
)
CREDENTIAL_IDENTIFIERS = (
    "".join(("GOOGLE_", "CLIENT_SECRET")),
    "".join(("oauth_", "refresh_token")),
    "".join(("cached_", "access_token_value")),
)
GOOGLE_CLIENT_SECRET_VALUE = "".join(("GOC", "SPX-", "A" * 28))
HOUSEHOLD_MARKERS = (
    "".join(("ru", "ss")),
    "".join(("jai", "me")),
    "".join(("ave", "ry")),
    "".join(("person", "_one")),
    "".join(("person", "_two")),
)
PRIVATE_OWNER_MARKERS = (
    "".join(("wo", "lfe")),
    "".join(("rwo", "lfe5420")),
)
MANIFEST_PATH = Path("custom_components") / "resiyhome_health_sync" / "manifest.json"
PUBLIC_PYTHON_SYNTAX_FILES = (
    Path("custom_components/resiyhome_health_sync/__init__.py"),
    Path("custom_components/resiyhome_health_sync/api.py"),
    Path("custom_components/resiyhome_health_sync/config_flow.py"),
)
RELEASE_PNG_PATHS = (
    Path("assets/health-sync-by-resiyhome.png"),
    Path("custom_components/resiyhome_health_sync/brand/icon.png"),
    Path("custom_components/resiyhome_health_sync/brand/logo.png"),
)
RELEASE_PNG_SHA256 = {
    "assets/health-sync-by-resiyhome.png": (
        "8e42459f91d76389dc834ba7867d841289f48d79f8ec2b21d0e0760ccf28b024"
    ),
    "custom_components/resiyhome_health_sync/brand/icon.png": (
        "0735521d031036b135fba5a1bbb117ef4a598abb5c5925aa5edf303866b20cfc"
    ),
    "custom_components/resiyhome_health_sync/brand/logo.png": (
        "8e42459f91d76389dc834ba7867d841289f48d79f8ec2b21d0e0760ccf28b024"
    ),
}


def _required_codeowner_declaration(*, prefix: str = "") -> str:
    handle = "@" + "".join(("rwo", "lfe5420"))
    return f'{prefix}  "codeowners": ["{handle}"],'


def _manifest_diff_header() -> str:
    path = MANIFEST_PATH.as_posix()
    return f"diff --git a/{path} b/{path}"


def _write_png(
    path: Path,
    metadata: str | None = None,
    *,
    exif_user_comment: bytes | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    png_info = PngImagePlugin.PngInfo()
    if metadata is not None:
        png_info.add_text("Comment", metadata)
    exif = Image.Exif()
    if exif_user_comment is not None:
        exif[ExifTags.IFD.Exif] = {ExifTags.Base.UserComment: exif_user_comment}
    Image.new("RGB", (2, 2), color="white").save(path, pnginfo=png_info, exif=exif)


def _insert_png_chunk(data: bytes, chunk_type: bytes, payload: bytes) -> bytes:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(chunk_type) == 4
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        existing_type = data[offset + 4 : offset + 8]
        if existing_type == b"IEND":
            checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
            chunk = (
                len(payload).to_bytes(4, "big")
                + chunk_type
                + payload
                + checksum.to_bytes(4, "big")
            )
            return data[:offset] + chunk + data[offset:]
        offset += length + 12
    raise AssertionError("PNG has no IEND chunk")


def _initialize_git_repository(repository: Path) -> None:
    repository.mkdir()
    commands = (
        ("git", "init"),
        ("git", "config", "user.name", "Synthetic Tester"),
        ("git", "config", "user.email", "@".join(("tester", "example.invalid"))),
    )
    for command in commands:
        subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)


def _commit_all(repository: Path, message: str) -> None:
    subprocess.run(("git", "add", "."), cwd=repository, check=True)
    subprocess.run(
        ("git", "commit", "-m", message),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _mock_history_commands(run, history: str) -> None:
    def command_result(command, **_kwargs):
        if command == [
            "git",
            "log",
            "--format=%H%n%B",
            "-m",
            "-p",
            "--all",
            "--no-ext-diff",
        ]:
            return subprocess.CompletedProcess(command, 0, stdout=history, stderr="")
        if command == ["git", "rev-list", "--all"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    run.side_effect = command_result


def _create_shared_blob_merge(repository: Path, content: str) -> bytes:
    _initialize_git_repository(repository)
    (repository / ".gitattributes").write_text("*.json binary\n", encoding="utf-8")
    (repository / "base.txt").write_text("neutral base", encoding="utf-8")
    _commit_all(repository, "add base")
    base_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subprocess.run(
        ("git", "switch", "-c", "manifest-copy"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = repository / MANIFEST_PATH
    manifest.parent.mkdir(parents=True)
    manifest.write_text(content, encoding="utf-8")
    _commit_all(repository, "add manifest copy")

    subprocess.run(
        ("git", "switch", "-c", "ordinary-copies", base_commit),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    (repository / "metadata.json").write_text(content, encoding="utf-8")
    _commit_all(repository, "add ordinary copies")

    subprocess.run(
        ("git", "switch", "manifest-copy"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ("git", "merge", "--no-ff", "ordinary-copies", "-m", "merge shared copies"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return content.encode("utf-8")


@pytest.mark.parametrize(
    "marker",
    (
        "".join(("Sher", "wood")),
        "".join(("sher", "wood", "_health")),
        "".join(("Sher", "wood", " Health")),
        "".join(("Sher", "wood", "Health")),
        "".join(("sher", "wood", "-google-health")),
    ),
)
def test_scanner_rejects_forbidden_identifiers(tmp_path: Path, marker: str) -> None:
    (tmp_path / "bad.txt").write_text(marker, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="forbidden identifier"):
        scan_tree(tmp_path)


def test_scanner_rejects_email_shape(tmp_path: Path) -> None:
    (tmp_path / "bad.txt").write_text("person" + "@example.com", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="email address"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
@pytest.mark.parametrize("separator", (":", "="))
def test_scanner_rejects_unquoted_key_and_value(
    tmp_path: Path, key: str, separator: str
) -> None:
    credential = f"{key}{separator}synthetic-value"
    (tmp_path / "bad.txt").write_text(credential, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_rejects_python_assignment_with_quoted_value(tmp_path: Path, key: str) -> None:
    (tmp_path / "bad.py").write_text(f'{key} = "synthetic-value"', encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_rejects_json_quoted_key_and_value(tmp_path: Path, key: str) -> None:
    (tmp_path / "bad.json").write_text(f'{{"{key}": "synthetic-value"}}', encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_rejects_python_single_quoted_key_and_value(tmp_path: Path, key: str) -> None:
    (tmp_path / "bad.py").write_text(f"{{'{key}': 'synthetic-value'}}", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_rejects_multiline_json_credential_assignment(
    tmp_path: Path, key: str
) -> None:
    credential = f'{{\n  "{key}"\n  :\n  "synthetic-value"\n}}'
    json.loads(credential)
    (tmp_path / "bad.json").write_text(credential, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_rejects_multiline_python_keyword_assignment(
    tmp_path: Path, key: str
) -> None:
    credential = f'configure(\n  {key}\n  =\n  "synthetic-value",\n)'
    ast.parse(credential)
    (tmp_path / "bad.py").write_text(credential, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
@pytest.mark.parametrize("suffix", ("yaml", "py"))
def test_scanner_rejects_multiline_yaml_credential_mapping(
    tmp_path: Path, key: str, suffix: str
) -> None:
    credential = f"{key}:\n  synthetic-live-value\n"
    assert yaml.safe_load(credential) == {key: "synthetic-live-value"}
    (tmp_path / f"bad.{suffix}").write_text(credential, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
@pytest.mark.parametrize("separator", (":", "="))
def test_scanner_rejects_quoted_key_and_unquoted_value(
    tmp_path: Path, key: str, separator: str
) -> None:
    (tmp_path / "bad.txt").write_text(f'"{key}"{separator}12345', encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("identifier", CREDENTIAL_IDENTIFIERS)
@pytest.mark.parametrize("separator", (":", "="))
def test_scanner_rejects_prefixed_or_suffixed_credential_assignments(
    tmp_path: Path, identifier: str, separator: str
) -> None:
    (tmp_path / "bad.txt").write_text(
        f'{identifier}{separator}"synthetic-live-value"',
        encoding="utf-8",
    )

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


def test_scanner_rejects_authorization_bearer_value(tmp_path: Path) -> None:
    header = "".join(("Authorization", ": Bearer ", "A" * 32))
    (tmp_path / "bad.txt").write_text(header, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize(
    "token",
    (
        "".join(("ya", "29.", "A" * 32)),
        "".join(("1", "//", "A" * 32)),
    ),
)
def test_scanner_rejects_google_token_values(tmp_path: Path, token: str) -> None:
    (tmp_path / "bad.txt").write_text(f"token value: {token}", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize(
    "template",
    (
        "{token}",
        'credential_value = "{token}"',
        '"generic_key": "{token}"',
    ),
)
def test_scanner_rejects_google_oauth_client_secret_values(
    tmp_path: Path, template: str
) -> None:
    (tmp_path / "bad.txt").write_text(
        template.format(token=GOOGLE_CLIENT_SECRET_VALUE),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


def test_scanner_allows_neutral_google_client_secret_references(tmp_path: Path) -> None:
    text = "\n".join(
        (
            "Google OAuth client secrets use the `GOCSPX-...` prefix.",
            "Use `GOCSPX-<secret>` as a placeholder only.",
            "The short `GOCSPX-example` text is not a credential value.",
        )
    )
    (tmp_path / "README.md").write_text(text, encoding="utf-8")

    scan_tree(tmp_path)


def test_scanner_allows_neutral_token_format_references(tmp_path: Path) -> None:
    text = "\n".join(
        (
            "Send the Authorization: Bearer <token> header.",
            "Google access tokens use the `ya29...` prefix.",
            "Google refresh tokens use the `1//...` prefix.",
            "The GOOGLE_CLIENT_SECRET and oauth_refresh_token names are always redacted.",
        )
    )
    (tmp_path / "README.md").write_text(text, encoding="utf-8")

    scan_tree(tmp_path)


def test_scanner_allows_documentation_that_only_names_credential_keys(tmp_path: Path) -> None:
    text = "\n".join(f"The `{key}` field is always redacted." for key in CREDENTIAL_KEYS)
    (tmp_path / "README.md").write_text(text, encoding="utf-8")

    scan_tree(tmp_path)


def test_scanner_allows_multiline_documentation_without_assignments(tmp_path: Path) -> None:
    text = "\n\n".join(
        f"The `{key}` field\ncontains a redacted credential when configured."
        for key in CREDENTIAL_KEYS
    )
    (tmp_path / "README.md").write_text(text, encoding="utf-8")

    scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_allows_multiline_python_control_flow(tmp_path: Path, key: str) -> None:
    code = f'if not {key}:\n    raise ValueError("credential unavailable")\n'
    ast.parse(code)
    (tmp_path / "check.py").write_text(code, encoding="utf-8")

    scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_allows_multiline_python_type_annotation(tmp_path: Path, key: str) -> None:
    code = f"def configure(\n    {key}:\n        str,\n) -> None:\n    pass\n"
    ast.parse(code)
    (tmp_path / "annotation.py").write_text(code, encoding="utf-8")

    scan_tree(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
@pytest.mark.parametrize(
    "template",
    (
        '{key}: str = "synthetic-live-value"\n',
        'def configure({key}: str = "synthetic-live-value") -> None:\n    pass\n',
    ),
)
def test_scanner_rejects_literal_values_hidden_by_python_annotations(
    tmp_path: Path, key: str, template: str
) -> None:
    code = template.format(key=key)
    ast.parse(code)
    (tmp_path / "annotated_value.py").write_text(code, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("relative_path", PUBLIC_PYTHON_SYNTAX_FILES)
def test_scanner_allows_verified_public_python_syntax(relative_path: Path) -> None:
    scan_text((ROOT / relative_path).read_text(encoding="utf-8"), relative_path.as_posix())


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_scanner_allows_empty_credential_placeholders(tmp_path: Path, key: str) -> None:
    (tmp_path / "example.json").write_text(f'{{"{key}": ""}}', encoding="utf-8")

    scan_tree(tmp_path)


@pytest.mark.parametrize("marker", HOUSEHOLD_MARKERS)
def test_scanner_rejects_household_markers_in_content(tmp_path: Path, marker: str) -> None:
    (tmp_path / "bad.txt").write_text(marker, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="forbidden identifier"):
        scan_tree(tmp_path)


def test_scanner_rejects_forbidden_relative_path_names(tmp_path: Path) -> None:
    marker = "".join(("Sher", "wood"))
    (tmp_path / f"{marker}-notes.txt").write_text("neutral", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="path name"):
        scan_tree(tmp_path)


def test_scanner_rejects_household_marker_in_relative_path_name(tmp_path: Path) -> None:
    marker = "".join(("ru", "ss"))
    path = tmp_path / marker / "notes.txt"
    path.parent.mkdir()
    path.write_text("neutral", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="path name"):
        scan_tree(tmp_path)


def test_scanner_rejects_local_user_paths(tmp_path: Path) -> None:
    local_path = "/" + "Users/" + "example/project"
    (tmp_path / "bad.txt").write_text(local_path, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="local user path"):
        scan_tree(tmp_path)


def test_scanner_allows_case_sensitive_google_api_user_path(tmp_path: Path) -> None:
    (tmp_path / "api.txt").write_text("/users/me/dataTypes/steps", encoding="utf-8")

    scan_tree(tmp_path)


def test_scanner_allows_only_required_manifest_codeowner_declaration(tmp_path: Path) -> None:
    path = tmp_path / MANIFEST_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        f'{{\n{_required_codeowner_declaration()}\n  "domain": "neutral"\n}}\n',
        encoding="utf-8",
    )

    scan_tree(tmp_path)


def test_scanner_rejects_required_declaration_outside_manifest(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text(
        f"{{\n{_required_codeowner_declaration()}\n}}\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseViolation, match="forbidden identifier"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("marker", PRIVATE_OWNER_MARKERS)
def test_scanner_rejects_private_owner_marker_in_manifest_content(
    tmp_path: Path, marker: str
) -> None:
    path = tmp_path / MANIFEST_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        f'{{\n{_required_codeowner_declaration()}\n  "maintainer": "{marker}"\n}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ReleaseViolation, match="forbidden identifier"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("marker", PRIVATE_OWNER_MARKERS)
def test_scanner_rejects_private_owner_marker_in_content(tmp_path: Path, marker: str) -> None:
    (tmp_path / "bad.txt").write_text(marker, encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="forbidden identifier"):
        scan_tree(tmp_path)


@pytest.mark.parametrize("marker", PRIVATE_OWNER_MARKERS)
def test_scanner_rejects_private_owner_marker_in_path(tmp_path: Path, marker: str) -> None:
    (tmp_path / f"{marker}-notes.txt").write_text("neutral", encoding="utf-8")

    with pytest.raises(ReleaseViolation, match="path name"):
        scan_tree(tmp_path)


def test_scanner_accepts_neutral_tree(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "Health Sync by ResiyHome",
        encoding="utf-8",
    )

    scan_tree(tmp_path)


@pytest.mark.parametrize(
    "metadata",
    (
        "".join(("Sher", "wood")),
        "person" + "@example.com",
        "/" + "Users/example/project",
        "".join(("GOOGLE_", "CLIENT_SECRET", '="synthetic-live-value"')),
        "".join(("ya", "29.", "A" * 32)),
    ),
)
def test_scanner_rejects_unsafe_png_text_metadata(tmp_path: Path, metadata: str) -> None:
    path = tmp_path / RELEASE_PNG_PATHS[0]
    _write_png(path, metadata)

    with pytest.raises(ReleaseViolation):
        verify_public_release._scan_png(path.read_bytes(), str(path))


def test_scanner_accepts_valid_release_png_assets(tmp_path: Path) -> None:
    assert ALLOWED_PNG_PATHS == frozenset(path.as_posix() for path in RELEASE_PNG_PATHS)
    assert verify_public_release.EXPECTED_PNG_SHA256 == RELEASE_PNG_SHA256
    for relative_path in RELEASE_PNG_PATHS:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == RELEASE_PNG_SHA256[
            relative_path.as_posix()
        ]

    scan_tree(tmp_path)


@pytest.mark.parametrize(
    ("chunk_type", "payload"),
    (
        (b"tEXt", b"Raw profile type app1\0synthetic neutral APP1 profile"),
        (b"caBX", b"synthetic neutral caBX payload"),
    ),
    ids=("imagemagick-raw-profile-app1", "cabx"),
)
def test_scanner_rejects_valid_shipped_png_with_added_chunk(
    tmp_path: Path, chunk_type: bytes, payload: bytes
) -> None:
    relative_path = RELEASE_PNG_PATHS[0]
    original = (ROOT / relative_path).read_bytes()
    modified = _insert_png_chunk(original, chunk_type, payload)
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(modified)

    assert modified != original
    with Image.open(target) as image:
        image.verify()
    with pytest.raises(ReleaseViolation, match="PNG SHA-256 mismatch"):
        scan_tree(tmp_path)


def test_scanner_rejects_shipped_png_at_wrong_allowlisted_path(tmp_path: Path) -> None:
    target = tmp_path / RELEASE_PNG_PATHS[1]
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / RELEASE_PNG_PATHS[0]).read_bytes())

    with Image.open(target) as image:
        image.verify()
    with pytest.raises(ReleaseViolation, match="PNG SHA-256 mismatch"):
        scan_tree(tmp_path)


def test_scanner_rejects_token_in_nested_exif_user_comment(tmp_path: Path) -> None:
    path = tmp_path / RELEASE_PNG_PATHS[0]
    token = "".join(("1", "//", "A" * 32))
    comment = b"ASCII\0\0\0" + token.encode("ascii")
    _write_png(path, exif_user_comment=comment)

    with Image.open(path) as image:
        nested = image.getexif().get_ifd(ExifTags.IFD.Exif)
        assert nested[ExifTags.Base.UserComment] == comment
    with pytest.raises(ReleaseViolation, match="credential-shaped value"):
        verify_public_release._scan_png(path.read_bytes(), str(path))


def test_scanner_accepts_clean_nested_exif_metadata(tmp_path: Path) -> None:
    path = tmp_path / RELEASE_PNG_PATHS[0]
    comment = b"ASCII\0\0\0clean synthetic release metadata"
    _write_png(path, exif_user_comment=comment)

    with Image.open(path) as image:
        nested = image.getexif().get_ifd(ExifTags.IFD.Exif)
        assert nested[ExifTags.Base.UserComment] == comment
    verify_public_release._scan_png(path.read_bytes(), str(path))


def test_scanner_rejects_invalid_allowlisted_png(tmp_path: Path) -> None:
    path = tmp_path / RELEASE_PNG_PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\ninvalid")

    with pytest.raises(ReleaseViolation, match="invalid PNG"):
        verify_public_release._scan_png(path.read_bytes(), str(path))


def test_scanner_rejects_png_outside_allowlist(tmp_path: Path) -> None:
    _write_png(tmp_path / "docs" / "diagram.png")

    with pytest.raises(ReleaseViolation, match="unsupported binary public file"):
        scan_tree(tmp_path)


def test_scanner_rejects_unsupported_binary_public_file(tmp_path: Path) -> None:
    (tmp_path / "archive.bin").write_bytes(b"\x00\xff\x00\xfe")

    with pytest.raises(ReleaseViolation, match="unsupported binary public file"):
        scan_tree(tmp_path)


def test_history_scan_excludes_author_email_metadata(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _initialize_git_repository(repository)
    (repository / "neutral.txt").write_text("neutral", encoding="utf-8")
    _commit_all(repository, "add neutral content")

    scan_history(repository)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_history_scan_rejects_multiline_yaml_credential_mapping(
    tmp_path: Path, key: str
) -> None:
    history = "\n".join(
        (
            "diff --git a/config.yaml b/config.yaml",
            f"+{key}:",
            "+  synthetic-live-value",
        )
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        with pytest.raises(ReleaseViolation, match="reachable Git history"):
            scan_history(tmp_path)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_history_scan_allows_verified_python_credential_syntax(
    tmp_path: Path, key: str
) -> None:
    code = (
        f"def configure(\n    {key}: str,\n) -> None:\n"
        f'    if not {key}:\n        raise ValueError("credential unavailable")\n'
    )
    ast.parse(code)
    history = "\n".join(
        (
            "diff --git a/module.py b/module.py",
            *(f"+{line}" for line in code.splitlines()),
        )
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        scan_history(tmp_path)


def test_history_scan_allows_only_exact_codeowner_declaration_line(tmp_path: Path) -> None:
    history = "\n".join(
        (_manifest_diff_header(), _required_codeowner_declaration(prefix="+"))
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        scan_history(tmp_path)


def test_history_scan_rejects_required_declaration_in_other_file(tmp_path: Path) -> None:
    history = "\n".join(
        (
            "diff --git a/metadata.json b/metadata.json",
            _required_codeowner_declaration(prefix="+"),
        )
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        with pytest.raises(ReleaseViolation, match="reachable Git history"):
            scan_history(tmp_path)


def test_history_scan_resets_manifest_exception_at_commit_boundary(tmp_path: Path) -> None:
    history = "\n".join(
        (
            _manifest_diff_header(),
            _required_codeowner_declaration(prefix="+"),
            "a" * 40,
            _required_codeowner_declaration(prefix="+"),
        )
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        with pytest.raises(ReleaseViolation, match="reachable Git history"):
            scan_history(tmp_path)


@pytest.mark.parametrize("marker", PRIVATE_OWNER_MARKERS)
def test_history_scan_rejects_private_owner_marker_outside_declaration(
    tmp_path: Path, marker: str
) -> None:
    history = "\n".join(
        (
            _manifest_diff_header(),
            _required_codeowner_declaration(prefix="+"),
            f'+  "maintainer": "{marker}"',
        )
    )
    with patch("scripts.verify_public_release.subprocess.run") as run:
        _mock_history_commands(run, history)

        with pytest.raises(ReleaseViolation, match="reachable Git history"):
            scan_history(tmp_path)


def test_history_scan_rejects_modified_earlier_reachable_png_blob(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _initialize_git_repository(repository)
    tracked = repository / RELEASE_PNG_PATHS[0]
    original = (ROOT / RELEASE_PNG_PATHS[0]).read_bytes()
    modified = _insert_png_chunk(original, b"caBX", b"historical neutral payload")
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(modified)
    _commit_all(repository, "add historical image")
    tracked.write_bytes(original)
    _commit_all(repository, "replace historical image")

    with Image.open(tracked) as image:
        image.verify()
    scan_tree(repository)

    with pytest.raises(ReleaseViolation, match="PNG SHA-256 mismatch"):
        scan_history(repository)


def test_history_scan_applies_png_hash_policy_per_allowlisted_path(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _initialize_git_repository(repository)
    shared = (ROOT / RELEASE_PNG_PATHS[0]).read_bytes()
    for relative_path in (RELEASE_PNG_PATHS[0], RELEASE_PNG_PATHS[1]):
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(shared)
    _commit_all(repository, "add shared PNG at two allowlisted paths")

    source_blob = subprocess.run(
        ("git", "rev-parse", f"HEAD:{RELEASE_PNG_PATHS[0].as_posix()}"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    icon_blob = subprocess.run(
        ("git", "rev-parse", f"HEAD:{RELEASE_PNG_PATHS[1].as_posix()}"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_blob == icon_blob

    with pytest.raises(ReleaseViolation, match="PNG SHA-256 mismatch"):
        scan_history(repository)


def test_history_scan_rejects_unsupported_binary_blob(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _initialize_git_repository(repository)
    (repository / "archive.bin").write_bytes(b"\x00\xff\x00\xfe")
    _commit_all(repository, "add unsupported binary")

    with pytest.raises(ReleaseViolation, match="unsupported binary public file"):
        scan_history(repository)


def test_history_scan_rejects_shared_codeowner_blob_at_nonmanifest_merge_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    content = f'{{\n{_required_codeowner_declaration()}\n}}\n'
    _create_shared_blob_merge(repository, content)

    manifest_blob = subprocess.run(
        ("git", "rev-parse", f"HEAD:{MANIFEST_PATH.as_posix()}"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ordinary_blob = subprocess.run(
        ("git", "rev-parse", "HEAD:metadata.json"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert manifest_blob == ordinary_blob

    patches = subprocess.run(
        ["git", "log", "--format=%H%n%B", "-m", "-p", "--all", "--no-ext-diff"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert _required_codeowner_declaration() not in patches

    with pytest.raises(ReleaseViolation, match="reachable Git history blob metadata.json"):
        scan_history(repository)


def test_history_scan_caches_shared_blob_by_path_policy(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    content = '{"domain": "neutral"}\n'
    blob = _create_shared_blob_merge(repository, content)

    with patch(
        "scripts.verify_public_release._scan_file_bytes",
        wraps=verify_public_release._scan_file_bytes,
    ) as scan_file:
        scan_history(repository)

    shared_blob_scans = [call for call in scan_file.call_args_list if call.args[0] == blob]
    assert len(shared_blob_scans) == 2
