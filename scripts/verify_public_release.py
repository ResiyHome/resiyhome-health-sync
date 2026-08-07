"""Reject private identity and credential material from public releases."""

from __future__ import annotations

import ast
import hashlib
import io
import os
import re
import shlex
import subprocess
import sys
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path

from PIL import ExifTags, Image


class ReleaseViolation(RuntimeError):
    """Raised when public release content violates privacy or naming rules."""


_FORMER_NAME = "".join(("Sher", "wood"))
FORBIDDEN_IDENTIFIERS = (
    _FORMER_NAME,
    _FORMER_NAME.lower() + "_health",
    _FORMER_NAME + " Health",
    _FORMER_NAME + "Health",
    _FORMER_NAME.lower() + "-google-health",
    "".join(("ru", "ss")),
    "".join(("jai", "me")),
    "".join(("ave", "ry")),
    "".join(("person", "_one")),
    "".join(("person", "_two")),
    "".join(("wo", "lfe")),
    "".join(("rwo", "lfe5420")),
)
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][\w.+-]*@[\w.-]+\.[A-Za-z]{2,}")
LOCAL_USER_PATH = re.compile(re.escape("/" + "Users/"))
_CREDENTIAL_NAMES = (
    "".join(("access", "_token")),
    "".join(("refresh", "_token")),
    "".join(("client", "_secret")),
)
_CREDENTIAL_KEYS = "|".join(_CREDENTIAL_NAMES)
_CREDENTIAL_IDENTIFIER_PATTERN = rf"[A-Za-z0-9_]*(?:{_CREDENTIAL_KEYS})[A-Za-z0-9_]*"
_CREDENTIAL_IDENTIFIER = re.compile(rf"(?i){_CREDENTIAL_IDENTIFIER_PATTERN}\Z")
SECRET = re.compile(
    rf"(?i)(?P<key>[\"']{_CREDENTIAL_IDENTIFIER_PATTERN}[\"']|"
    rf"(?<![A-Za-z0-9_]){_CREDENTIAL_IDENTIFIER_PATTERN}(?![A-Za-z0-9_]))"
    r"\s*(?P<separator>:(?!//)|(?<![=!<>])=(?!=))\s*"
    r"(?P<value>\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s\"'=:{},\[\]]+)"
)
AUTHORIZATION_BEARER = re.compile(
    r"(?ix)(?<![A-Za-z0-9_])[\"']?authorization[\"']?\s*"
    r"(?::(?!//)|(?<![=!<>])=(?!=))\s*[\"']?bearer\s+"
    r"[A-Za-z0-9][A-Za-z0-9._~+/=-]{19,}"
)
GOOGLE_ACCESS_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9._-])ya29\.[A-Za-z0-9][A-Za-z0-9._-]{19,}"
)
GOOGLE_REFRESH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_/])1//[A-Za-z0-9][A-Za-z0-9_-]{19,}"
)
GOOGLE_OAUTH_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])GOCSPX-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_REFERENCE_PREFIXES = (
    "client.get(",
    "entry.data[",
    "entry.data.get(",
    "getattr(",
    "payload.get(",
    "state.",
    "token.get(",
)
_MANIFEST_SOURCE = "custom_components/resiyhome_health_sync/manifest.json"
_REQUIRED_CODEOWNER = "@" + "".join(("rwo", "lfe5420"))
_CODEOWNER_DECLARATION = f'  "codeowners": ["{_REQUIRED_CODEOWNER}"],'
_MANIFEST_DIFF_HEADER = f"diff --git a/{_MANIFEST_SOURCE} b/{_MANIFEST_SOURCE}"
_HISTORY_COMMIT_HEADER = re.compile(r"[0-9a-f]{40}")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXPECTED_PNG_SHA256 = {
    "assets/health-sync-by-resiyhome.png": (
        "8e42459f91d76389dc834ba7867d841289f48d79f8ec2b21d0e0760ccf28b024"
    ),
    "custom_components/resiyhome_health_sync/brand/logo.png": (
        "8e42459f91d76389dc834ba7867d841289f48d79f8ec2b21d0e0760ccf28b024"
    ),
    "custom_components/resiyhome_health_sync/brand/icon.png": (
        "0735521d031036b135fba5a1bbb117ef4a598abb5c5925aa5edf303866b20cfc"
    ),
}
ALLOWED_PNG_PATHS = frozenset(EXPECTED_PNG_SHA256)


def _is_credential_identifier(value: str) -> bool:
    """Return whether a Python identifier contains a credential field name."""
    return _CREDENTIAL_IDENTIFIER.fullmatch(value) is not None


def _without_approved_codeowner(text: str, source: str) -> str:
    """Remove only the required codeowner declaration from its approved location."""
    lines = text.splitlines(keepends=True)
    if source == _MANIFEST_SOURCE:
        return "".join(
            line[len(line.rstrip("\r\n")) :]
            if line.rstrip("\r\n") == _CODEOWNER_DECLARATION
            else line
            for line in lines
        )
    if source != "reachable Git history":
        return text

    sanitized: list[str] = []
    in_manifest_diff = False
    approved_diff_lines = {
        f" {_CODEOWNER_DECLARATION}",
        f"+{_CODEOWNER_DECLARATION}",
        f"-{_CODEOWNER_DECLARATION}",
    }
    for line in lines:
        content = line.rstrip("\r\n")
        if _HISTORY_COMMIT_HEADER.fullmatch(content):
            in_manifest_diff = False
        elif content.startswith("diff --git "):
            in_manifest_diff = content == _MANIFEST_DIFF_HEADER
        if in_manifest_diff and content in approved_diff_lines:
            sanitized.append(line[len(content) :])
        else:
            sanitized.append(line)
    return "".join(sanitized)


def _safe_annotation_default(value: ast.expr | None) -> bool:
    """Return whether an annotation default cannot itself be a literal credential."""
    return value is None or not isinstance(value, ast.Constant) or value.value in {None, ""}


def _verified_python_nodes(tree: ast.AST) -> Iterator[ast.AST]:
    """Yield credential names used by verified control-flow or annotation syntax."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            for child in ast.walk(node.test):
                if isinstance(child, ast.Name) and _is_credential_identifier(child.id):
                    yield child
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and _is_credential_identifier(node.target.id)
                and _safe_annotation_default(node.value)
            ):
                yield node.target
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = (*node.args.posonlyargs, *node.args.args)
            positional_defaults = (
                *(None for _ in range(len(positional) - len(node.args.defaults))),
                *node.args.defaults,
            )
            annotated = (*zip(positional, positional_defaults, strict=True),)
            annotated += (*zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True),)
            for argument, default in annotated:
                if (
                    argument.annotation is not None
                    and _is_credential_identifier(argument.arg)
                    and _safe_annotation_default(default)
                ):
                    yield argument


def _python_source_offsets(text: str, source: str) -> set[int]:
    """Return offsets of verified credential names in a Python source file."""
    if Path(source).suffix != ".py":
        return set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()

    line_offsets: list[int] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        line_offsets.append(offset)
        offset += len(line)
    return {
        line_offsets[node.lineno - 1] + node.col_offset
        for node in _verified_python_nodes(tree)
        if node.lineno <= len(line_offsets)
    }


def _history_python_path(text: str, offset: int) -> bool:
    """Return whether an offset belongs to a Python file's Git diff section."""
    header_start = text.rfind("\ndiff --git ", 0, offset)
    if header_start < 0:
        header_start = 0 if text.startswith("diff --git ") else -1
    if header_start < 0:
        return False
    header_end = text.find("\n", header_start + 1)
    header = text[header_start + (1 if text[header_start] == "\n" else 0) : header_end]
    try:
        parts = shlex.split(header)
    except ValueError:
        return False
    return len(parts) == 4 and parts[0:2] == ["diff", "--git"] and parts[3].endswith(".py")


def _history_python_line_is_verified(text: str, match: re.Match[str]) -> bool:
    """Validate a credential colon match against its Python patch line syntax."""
    if not _history_python_path(text, match.start("key")):
        return False
    line_start = text.rfind("\n", 0, match.start("key")) + 1
    line_end = text.find("\n", match.start("key"))
    if line_end < 0:
        line_end = len(text)
    patch_line = text[line_start:line_end]
    if patch_line[:1] not in {"+", "-", " "}:
        return False

    code = textwrap.dedent(patch_line[1:]).strip()
    name = match.group("key").lower()
    candidates = [code]
    if code.startswith("elif "):
        candidates.append("if " + code.removeprefix("elif ") + "\n    pass")
    elif code.startswith(("if ", "while ")) and code.endswith(":"):
        candidates.append(code + "\n    pass")
    elif code.startswith(("def ", "async def ")) and code.endswith(":"):
        candidates.append(code + "\n    pass")
    candidates.append(f"def _verified(\n    {code}\n):\n    pass")

    for candidate in candidates:
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        if any(
            isinstance(node, (ast.Name, ast.arg))
            and getattr(node, "id", getattr(node, "arg", "")).lower() == name
            for node in _verified_python_nodes(tree)
        ):
            return True
    return False


def _verified_python_offsets(text: str, source: str) -> set[int]:
    """Return credential-name offsets proven to be Python syntax, including patches."""
    if source != "reachable Git history":
        return _python_source_offsets(text, source)
    return {
        match.start("key")
        for match in SECRET.finditer(text)
        if match.group("separator") == ":"
        and not match.group("key").startswith(("\"", "'"))
        and _history_python_line_is_verified(text, match)
    }


def _is_code_reference(match: re.Match[str], verified_python_offsets: set[int]) -> bool:
    """Return whether a credential-shaped match is only a Python field reference."""
    key = match.group("key")
    name = key.strip("\"'").lower()
    separator = match.group("separator")
    value = match.group("value").rstrip(",)}]")
    lowered = value.lower()
    reference_name = lowered.rsplit(".", maxsplit=1)[-1].lstrip("_")
    same_credential_kind = _is_credential_identifier(reference_name) and any(
        credential_name in name and credential_name in reference_name
        for credential_name in _CREDENTIAL_NAMES
    )

    if separator == ":" and not key.startswith(("\"", "'")):
        return match.start("key") in verified_python_offsets
    if value.startswith(("\"", "'")):
        return False
    return (
        lowered == "none"
        or lowered == "false"
        or lowered == name
        or same_credential_kind
        or lowered == "entry.data"
        or lowered.endswith(f".{name}")
        or lowered.endswith(f"._{name}")
        or lowered.startswith(_REFERENCE_PREFIXES)
    )


def scan_text(text: str, source: str, *, codeowner_source: str | None = None) -> None:
    """Scan text for private identity, email, and credential material."""
    text = _without_approved_codeowner(text, codeowner_source or source)
    if LOCAL_USER_PATH.search(text):
        raise ReleaseViolation(f"local user path in {source}")
    lowered = text.lower()
    for marker in FORBIDDEN_IDENTIFIERS:
        if marker.lower() in lowered:
            raise ReleaseViolation(f"forbidden identifier in {source}")
    if EMAIL.search(text):
        raise ReleaseViolation(f"email address in {source}")
    verified_python_offsets = _verified_python_offsets(text, source)
    if any(
        not _is_code_reference(match, verified_python_offsets) for match in SECRET.finditer(text)
    ):
        raise ReleaseViolation(f"credential-shaped value in {source}")
    if any(
        pattern.search(text) is not None
        for pattern in (
            AUTHORIZATION_BEARER,
            GOOGLE_ACCESS_PATTERN,
            GOOGLE_REFRESH_PATTERN,
            GOOGLE_OAUTH_SECRET_PATTERN,
        )
    ):
        raise ReleaseViolation(f"credential-shaped value in {source}")


def _decoded_metadata_texts(data: bytes) -> tuple[str, ...]:
    """Decode only byte payloads that plausibly represent textual metadata."""
    prefixed_encodings = (
        (b"ASCII\0\0\0", ("ascii", "utf-8")),
        (b"JIS\0\0\0\0\0", ("shift_jis",)),
        (b"UNICODE\0", ("utf-16", "utf-16-le", "utf-16-be")),
    )
    payload = data
    encodings: tuple[str, ...] = ("utf-8-sig",)
    for prefix, prefix_encodings in prefixed_encodings:
        if data.startswith(prefix):
            payload = data[len(prefix) :]
            encodings = prefix_encodings
            break
    else:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\0" in data:
            encodings += ("utf-16", "utf-16-le", "utf-16-be")

    decoded: list[str] = []
    for encoding in encodings:
        try:
            text = payload.decode(encoding).strip("\0")
        except (UnicodeDecodeError, UnicodeError):
            continue
        if not text:
            continue
        textual = sum(character.isprintable() or character in "\r\n\t" for character in text)
        if textual / len(text) < 0.85 or text in decoded:
            continue
        decoded.append(text)
    return tuple(decoded)


def _scan_metadata_value(value: object, source: str, seen: set[int] | None = None) -> None:
    """Recursively scan textual and safely decoded byte metadata values."""
    if isinstance(value, str):
        scan_text(value, source)
        return
    if isinstance(value, bytes | bytearray | memoryview):
        for text in _decoded_metadata_texts(bytes(value)):
            scan_text(text, source)
        return

    if not isinstance(value, Mapping | list | tuple | set | frozenset):
        return
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))

    if isinstance(value, Mapping):
        for key, nested in value.items():
            _scan_metadata_value(key, f"{source} key", seen)
            _scan_metadata_value(nested, f"{source} value", seen)
    else:
        for nested in value:
            _scan_metadata_value(nested, source, seen)


def _scan_png(data: bytes, source: str) -> None:
    """Validate an allowlisted PNG and recursively scan Pillow-decoded metadata."""
    if not data.startswith(PNG_SIGNATURE):
        raise ReleaseViolation(f"invalid PNG signature in {source}")

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG":
                raise ReleaseViolation(f"invalid PNG format in {source}")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            exif = image.getexif()
            nested_ifds: dict[str, dict[int, object]] = {}
            for ifd in ExifTags.IFD:
                try:
                    nested = exif.get_ifd(ifd)
                except KeyError:
                    continue
                if nested:
                    nested_ifds[ifd.name] = nested
            metadata = {
                "PNG info": dict(image.info),
                "PNG text": dict(getattr(image, "text", {})),
                "EXIF root": dict(exif),
                "EXIF IFDs": nested_ifds,
            }
    except ReleaseViolation:
        raise
    except Exception as err:
        raise ReleaseViolation(f"invalid PNG in {source}") from err

    _scan_metadata_value(metadata, f"PNG metadata in {source}")


def _scan_file_bytes(
    data: bytes,
    relative_path: str,
    source: str,
    *,
    codeowner_source: str | None = None,
) -> None:
    """Apply the public text or allowlisted-binary policy to one file."""
    if Path(relative_path).suffix.lower() == ".png":
        expected_digest = EXPECTED_PNG_SHA256.get(relative_path)
        if expected_digest is None:
            raise ReleaseViolation(f"unsupported binary public file in {source}")
        if hashlib.sha256(data).hexdigest() != expected_digest:
            raise ReleaseViolation(f"PNG SHA-256 mismatch in {source}")
        _scan_png(data, source)
        return

    if b"\0" in data:
        raise ReleaseViolation(f"unsupported binary public file in {source}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ReleaseViolation(f"unsupported binary public file in {source}") from err
    scan_text(text, source, codeowner_source=codeowner_source)


def scan_tree(root: Path) -> None:
    """Scan public text and explicitly validated binary files below a release root."""
    for path in sorted(root.rglob("*")):
        if SKIP_PARTS.intersection(path.parts):
            continue
        relative_path = path.relative_to(root)
        scan_text(relative_path.as_posix(), f"path name {relative_path.as_posix()}")
        if not path.is_file():
            continue
        _scan_file_bytes(path.read_bytes(), relative_path.as_posix(), str(relative_path))


def scan_history(root: Path) -> None:
    """Scan patches and commit/path/blob associations reachable from HEAD."""
    history = subprocess.run(
        [
            "git",
            "log",
            "--format=%H%n%B",
            "-m",
            "-p",
            "HEAD",
            "--no-ext-diff",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    scan_text(history.stdout, "reachable Git history")

    commits = subprocess.run(
        ["git", "rev-list", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    blob_bytes: dict[str, bytes] = {}
    scanned_blobs: set[tuple[str, tuple[str, str]]] = set()
    for commit in commits.stdout.splitlines():
        tree = subprocess.run(
            ["git", "ls-tree", "-rz", "--full-tree", commit],
            cwd=root,
            check=True,
            capture_output=True,
        )
        for record in tree.stdout.split(b"\0"):
            if not record:
                continue
            metadata, separator, path_bytes = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                raise ReleaseViolation(f"invalid Git tree record in {commit}")
            _mode, object_type, object_id_bytes = fields
            relative_path = os.fsdecode(path_bytes)
            scan_text(relative_path, f"reachable Git history path {relative_path}")
            if object_type != b"blob":
                continue

            object_id = object_id_bytes.decode("ascii")
            suffix = Path(relative_path).suffix.lower()
            if suffix == ".png":
                policy = ("png", relative_path)
            else:
                policy = (
                    "text",
                    "manifest" if relative_path == _MANIFEST_SOURCE else "ordinary",
                )
            cache_key = (object_id, policy)
            if cache_key in scanned_blobs:
                continue

            if object_id not in blob_bytes:
                blob_bytes[object_id] = subprocess.run(
                    ["git", "cat-file", "blob", object_id],
                    cwd=root,
                    check=True,
                    capture_output=True,
                ).stdout
            _scan_file_bytes(
                blob_bytes[object_id],
                relative_path,
                f"reachable Git history blob {relative_path}",
                codeowner_source=relative_path,
            )
            scanned_blobs.add(cache_key)


if __name__ == "__main__":
    repository = Path(sys.argv[1] if len(sys.argv) == 2 else ".").resolve()
    scan_tree(repository)
    scan_history(repository)
