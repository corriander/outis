"""Reference read-only ArtifactStore provider for directories containing GGUFs.

The provider is deliberately a separate HTTP process.  It may run on a host or
inside WSL where the model filesystem is visible while Outis remains in a
container with no mount of that filesystem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlsplit


SCHEMA_VERSION = 1
# Compatibility default for direct Python callers of ``inventory_document``.
# The network-facing CLI requires an explicit instance authority instead.
LEGACY_PROGRAMMATIC_PROVIDER_ID = "directory"
DEFAULT_PROVIDER_NAME = "Directory inventory"
PROVIDER_CLASS = "directory"
_ROOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SPLIT_GGUF_RE = re.compile(
    r"^(?P<prefix>.+)-(?P<part>\d{1,6})-of-(?P<total>\d{1,6})\.gguf$",
    re.IGNORECASE,
)
_TEMP_SUFFIXES = (".incomplete", ".partial", ".part", ".download", ".tmp")
_QUANT_RE = re.compile(
    r"(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)"
)
_TEMPLATE_TOKEN_RE = re.compile(r"\{(path|root|rel)\}")


@dataclass(frozen=True)
class PathVariantTemplate:
    """One publisher-defined opaque path variant.

    ``template`` may contain the tokens ``{path}`` (absolute host path of the
    primary file), ``{root}`` (absolute root path on the host) and ``{rel}``
    (POSIX relative path within the root). Any other content is preserved
    verbatim; Outis never parses or joins these strings.
    """

    label: str
    template: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("path variant label must not be empty")
        if not self.template:
            raise ValueError("path variant template must not be empty")


@dataclass(frozen=True)
class DirectoryRoot:
    """One provider-owned scan root and its display-safe public label."""

    id: str
    path: Path
    label: str
    path_variants: tuple[PathVariantTemplate, ...] = ()

    def __post_init__(self) -> None:
        if not _ROOT_ID_RE.fullmatch(self.id):
            raise ValueError(
                "root id must start with an alphanumeric and contain only "
                "letters, numbers, '.', '_' or '-' (maximum 64 characters)"
            )
        if not self.label.strip():
            raise ValueError("root label must not be empty")


@dataclass(frozen=True)
class _ObservedFile:
    relative_path: str
    filename: str
    intended_filename: str
    size_bytes: int
    modified_at: str
    temporary: bool = False


def _iso_mtime(stat_result: os.stat_result) -> str:
    return datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def _quantization(filename: str) -> str | None:
    match = _QUANT_RE.search(filename)
    return match.group(0).upper() if match else None


def _gguf_name(filename: str) -> tuple[str, bool] | None:
    """Return the intended GGUF filename and whether the observed file is temporary."""

    lower = filename.lower()
    if lower.endswith(".gguf"):
        return filename, False
    for suffix in _TEMP_SUFFIXES:
        if lower.endswith(".gguf" + suffix):
            return filename[: -len(suffix)], True
    return None


def _artifact_id(root_id: str, identity_path: str) -> str:
    normalized = identity_path.replace("\\", "/")
    # The readable path makes an ID diagnosable while the digest prevents two
    # case variants or unusual Unicode normalization from looking identical.
    digest = hashlib.sha256(f"{root_id}\0{normalized}".encode("utf-8")).hexdigest()[:12]
    readable = quote(normalized, safe="/._-")
    return f"{root_id}:{readable}:{digest}"


def _display_location(root: DirectoryRoot, relative_path: str) -> str:
    parts = [root.label.strip(), *Path(relative_path).parts]
    return " / ".join(str(part) for part in parts if str(part) not in {"", "."})


def _logical_path(relative_path: str) -> str:
    return Path(relative_path).as_posix()


def _observation_token(files: list[_ObservedFile]) -> str:
    fingerprint = [
        (item.filename, int(item.size_bytes), item.modified_at)
        for item in files
    ]
    fingerprint.sort()
    payload = json.dumps(fingerprint, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _render_template(template: str, *, root_path: str, rel_path: str, path: str) -> str:
    substitutions = {"path": path, "root": root_path, "rel": rel_path}

    def replace(match: re.Match[str]) -> str:
        return substitutions[match.group(1)]

    return _TEMPLATE_TOKEN_RE.sub(replace, template)


def _path_variants(
    root: DirectoryRoot,
    *,
    primary: _ObservedFile,
) -> list[dict]:
    if not root.path_variants:
        return []
    root_path = str(root.path)
    rel_path = _logical_path(primary.relative_path)
    # ``relative_path`` names the intended artifact for temporary downloads,
    # while path variants must report the file that was actually observed.
    observed_relative = Path(primary.relative_path).parent / primary.filename
    file_path = str(root.path / observed_relative)
    return [
        {
            "label": variant.label,
            "value": _render_template(
                variant.template,
                root_path=root_path,
                rel_path=rel_path,
                path=file_path,
            ),
        }
        for variant in root.path_variants
    ]


def _artifact(
    root: DirectoryRoot,
    *,
    identity_path: str,
    filename: str,
    relative_path: str,
    files: list[_ObservedFile],
    incomplete: bool,
    primary: _ObservedFile,
    split_total: int | None = None,
) -> dict:
    parent = Path(relative_path).parent
    group_path = [] if str(parent) == "." else list(parent.parts)
    observed = {
        "size_bytes": sum(item.size_bytes for item in files),
        "modified_at": max(item.modified_at for item in files),
        "format": "gguf",
        "quantization": _quantization(filename),
        "state": "incomplete" if incomplete else "ready",
    }
    if split_total is not None:
        observed["split"] = {
            "parts_present": len(files),
            "parts_expected": split_total,
        }
    artifact: dict = {
        "id": _artifact_id(root.id, identity_path),
        "source_id": root.id,
        "observation": _observation_token(files),
        "filename": filename,
        "logical_path": _logical_path(relative_path),
        "display_location": _display_location(root, relative_path),
        "group_path": group_path,
        "observed": observed,
        "files": [
            {
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "modified_at": item.modified_at,
            }
            for item in files
        ],
    }
    variants = _path_variants(root, primary=primary)
    if variants:
        artifact["path_variants"] = variants
    return artifact


def scan_directory_root(root: DirectoryRoot) -> tuple[list[dict], dict]:
    """Enumerate GGUF artifacts under one root without following symlinks."""

    source = {"id": root.id, "label": root.label, "state": "ready", "artifact_count": 0}
    try:
        if not root.path.is_dir():
            raise OSError("directory is not reachable")
    except OSError as exc:
        source.update({"state": "unreachable", "error": str(exc)})
        return [], source

    observed_files: list[_ObservedFile] = []
    walk_errors: list[OSError] = []
    try:
        for current, dirs, filenames in os.walk(
            root.path,
            followlinks=False,
            onerror=walk_errors.append,
        ):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            for filename in filenames:
                if filename.startswith("._"):
                    continue
                parsed = _gguf_name(filename)
                if parsed is None:
                    continue
                intended_name, temporary = parsed
                path = current_path / filename
                if path.is_symlink():
                    continue
                try:
                    stat_result = path.stat()
                except OSError:
                    continue
                relative = path.relative_to(root.path).as_posix()
                if temporary:
                    relative = relative[: -len(filename)] + intended_name
                observed_files.append(
                    _ObservedFile(
                        relative_path=relative,
                        filename=filename,
                        intended_filename=intended_name,
                        size_bytes=stat_result.st_size,
                        modified_at=_iso_mtime(stat_result),
                        temporary=temporary,
                    )
                )
    except OSError:
        walk_errors.append(OSError("scan interrupted"))
    if walk_errors:
        source.update({"state": "partial", "error": "one or more directories could not be read"})

    split_groups: dict[tuple[str, str, int], list[tuple[int, _ObservedFile]]] = {}
    ordinary_groups: dict[str, list[_ObservedFile]] = {}
    for item in observed_files:
        match = _SPLIT_GGUF_RE.match(item.intended_filename)
        if not match:
            ordinary_groups.setdefault(item.relative_path, []).append(item)
            continue
        parent = Path(item.relative_path).parent.as_posix()
        key = (parent, match.group("prefix"), int(match.group("total")))
        split_groups.setdefault(key, []).append((int(match.group("part")), item))

    ordinary: list[_ObservedFile] = []
    for candidates in ordinary_groups.values():
        completed = [item for item in candidates if not item.temporary]
        if completed:
            # Only one completed file can occupy a filesystem path. Prefer it
            # over any stale download marker for the same intended GGUF.
            ordinary.append(completed[0])
            continue
        # Multiple temporary suffixes can coexist. Keep one logical artifact
        # and use the newest observation, with filename as a stable tie-break.
        ordinary.append(max(candidates, key=lambda item: (item.modified_at, item.filename)))

    artifacts = [
        _artifact(
            root,
            identity_path=item.relative_path,
            filename=item.intended_filename,
            relative_path=item.relative_path,
            files=[item],
            incomplete=item.temporary,
            primary=item,
        )
        for item in ordinary
    ]
    for (parent, prefix, total), parts in split_groups.items():
        # A completed shard and its stale temporary predecessor can coexist.
        # Count the shard number once and prefer the completed observation.
        by_number: dict[int, _ObservedFile] = {}
        for number, item in sorted(parts, key=lambda pair: (pair[0], pair[1].temporary)):
            current = by_number.get(number)
            if current is None or (current.temporary and not item.temporary):
                by_number[number] = item
        numbered_files = sorted(by_number.items())
        files = [item for _, item in numbered_files]
        primary = next((item for number, item in numbered_files if number == 1), files[0])
        expected_parts = set(range(1, total + 1))
        present_parts = set(by_number)
        incomplete = expected_parts != present_parts or any(item.temporary for item in files)
        identity_name = f"{prefix}.gguf"
        identity_path = identity_name if parent == "." else f"{parent}/{identity_name}"
        artifacts.append(
            _artifact(
                root,
                identity_path=identity_path,
                filename=primary.intended_filename,
                relative_path=primary.relative_path,
                files=files,
                incomplete=incomplete,
                primary=primary,
                split_total=total,
            )
        )

    artifacts.sort(key=lambda item: (item["display_location"].casefold(), item["id"]))
    source["artifact_count"] = len(artifacts)
    return artifacts, source


def inventory_document(
    roots: Iterable[DirectoryRoot],
    *,
    provider_id: str = LEGACY_PROGRAMMATIC_PROVIDER_ID,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    provider_class: str | None = PROVIDER_CLASS,
) -> dict:
    provider_id = provider_id.strip()
    provider_name = provider_name.strip()
    if not provider_id:
        raise ValueError("provider id must not be empty")
    if not provider_name:
        raise ValueError("provider name must not be empty")
    artifacts: list[dict] = []
    sources: list[dict] = []
    for root in roots:
        found, source = scan_directory_root(root)
        artifacts.extend(found)
        sources.append(source)
    artifacts.sort(key=lambda item: (item["display_location"].casefold(), item["id"]))
    states = {source["state"] for source in sources}
    if sources and states == {"unreachable"}:
        state = "unreachable"
    elif "unreachable" in states or "partial" in states:
        state = "partial"
    else:
        state = "ready"
    provider: dict = {"id": provider_id, "name": provider_name}
    if provider_class:
        # ``class`` is advisory implementation-class metadata. Outis MUST NOT
        # use it for identity decisions; only ``id`` is authoritative.
        provider["class"] = provider_class
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "status": {
            "state": state,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sources": sources,
        },
        "artifacts": artifacts,
    }


def _parse_root(
    value: str,
    labels: dict[str, str],
    variants: dict[str, list[PathVariantTemplate]],
) -> DirectoryRoot:
    if "=" not in value:
        raise ValueError("--root must use ID=PATH")
    root_id, raw_path = value.split("=", 1)
    root_id = root_id.strip()
    raw_path = raw_path.strip()
    if not raw_path:
        raise ValueError("root path must not be empty")
    return DirectoryRoot(
        root_id,
        Path(raw_path).expanduser(),
        labels.get(root_id, root_id),
        tuple(variants.get(root_id, ())),
    )


def _parse_assignment(value: str, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"{option} must use ID=VALUE")
    key, assigned = value.split("=", 1)
    if not key.strip() or not assigned.strip():
        raise ValueError(f"{option} must use non-empty ID=VALUE")
    return key.strip(), assigned.strip()


def _parse_path_variant(value: str) -> tuple[str, PathVariantTemplate]:
    """Parse ``--path-variant ROOT_ID:LABEL=TEMPLATE``.

    Splits on the first ``:`` for the root id, then on the first ``=`` in the
    remainder for the label and template. TEMPLATE may contain further ``:``
    and ``=`` characters (e.g. Windows drive prefixes).
    """

    if ":" not in value:
        raise ValueError("--path-variant must use ROOT_ID:LABEL=TEMPLATE")
    root_id, remainder = value.split(":", 1)
    if "=" not in remainder:
        raise ValueError("--path-variant must use ROOT_ID:LABEL=TEMPLATE")
    label, template = remainder.split("=", 1)
    if not root_id.strip() or not label.strip() or not template:
        raise ValueError("--path-variant must use non-empty ROOT_ID:LABEL=TEMPLATE")
    return root_id.strip(), PathVariantTemplate(label.strip(), template)


def make_handler(
    roots: list[DirectoryRoot],
    provider_id: str,
    provider_name: str,
    token: str | None,
    provider_class: str | None = PROVIDER_CLASS,
):
    class ArtifactRequestHandler(BaseHTTPRequestHandler):
        server_version = "OutisArtifactStore/1"

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(HTTPStatus.OK, {"ok": True, "schema_version": SCHEMA_VERSION})
                return
            if path != "/v1/artifacts":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if token:
                authorization = self.headers.get("Authorization", "")
                supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not supplied or not secrets.compare_digest(supplied, token):
                    self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return
            self._send_json(
                HTTPStatus.OK,
                inventory_document(
                    roots,
                    provider_id=provider_id,
                    provider_name=provider_name,
                    provider_class=provider_class,
                ),
            )

        def log_message(self, fmt: str, *args) -> None:
            print(f"[artifact-provider] {self.address_string()} {fmt % args}")

    return ArtifactRequestHandler


def _is_loopback(bind: str) -> bool:
    return bind.strip().lower() in {"127.0.0.1", "::1", "localhost"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a read-only GGUF ArtifactStore inventory")
    parser.add_argument("--root", action="append", required=True, metavar="ID=PATH")
    parser.add_argument("--label", action="append", default=[], metavar="ID=LABEL")
    parser.add_argument(
        "--path-variant",
        action="append",
        default=[],
        metavar="ROOT_ID:LABEL=TEMPLATE",
        help=(
            "Publish an opaque path variant for artifacts in a root. TEMPLATE "
            "may contain {path}, {root} and {rel} tokens. Repeatable."
        ),
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument(
        "--provider-id",
        required=True,
        help=(
            "Stable instance authority for ArtifactRefs (opaque to clients)."
        ),
    )
    parser.add_argument("--provider-name", default=DEFAULT_PROVIDER_NAME)
    parser.add_argument(
        "--provider-class",
        default=PROVIDER_CLASS,
        help="Advisory implementation-class hint published in provider.class.",
    )
    args = parser.parse_args(argv)

    try:
        labels = dict(_parse_assignment(value, "--label") for value in args.label)
        variants: dict[str, list[PathVariantTemplate]] = {}
        for raw in args.path_variant:
            root_id, template = _parse_path_variant(raw)
            variants.setdefault(root_id, []).append(template)
        roots = [_parse_root(value, labels, variants) for value in args.root]
    except ValueError as exc:
        parser.error(str(exc))
    known_ids = {root.id for root in roots}
    for orphan in sorted(set(variants) - known_ids):
        parser.error(f"--path-variant references unknown root id {orphan!r}")
    token = os.getenv("OUTIS_ARTIFACT_PROVIDER_TOKEN", "").strip() or None
    if not _is_loopback(args.bind) and token is None:
        parser.error("OUTIS_ARTIFACT_PROVIDER_TOKEN is required when binding beyond loopback")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    provider_id = args.provider_id.strip()
    provider_name = args.provider_name.strip()
    if not provider_id:
        parser.error("--provider-id must not be empty")
    if not provider_name:
        parser.error("--provider-name must not be empty")
    provider_class = args.provider_class.strip() or None
    server = ThreadingHTTPServer(
        (args.bind, args.port),
        make_handler(roots, provider_id, provider_name, token, provider_class),
    )
    print(f"[artifact-provider] serving {len(roots)} root(s) on http://{args.bind}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
