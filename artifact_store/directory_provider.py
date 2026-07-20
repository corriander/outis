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
_ROOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SPLIT_GGUF_RE = re.compile(
    r"^(?P<prefix>.+)-(?P<part>\d{1,6})-of-(?P<total>\d{1,6})\.gguf$",
    re.IGNORECASE,
)
_TEMP_SUFFIXES = (".incomplete", ".partial", ".part", ".download", ".tmp")
_QUANT_RE = re.compile(
    r"(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)"
)


@dataclass(frozen=True)
class DirectoryRoot:
    """One provider-owned scan root and its display-safe public label."""

    id: str
    path: Path
    label: str

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


def _artifact(
    root: DirectoryRoot,
    *,
    identity_path: str,
    filename: str,
    relative_path: str,
    files: list[_ObservedFile],
    incomplete: bool,
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
    return {
        "id": _artifact_id(root.id, identity_path),
        "filename": filename,
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
    ordinary: list[_ObservedFile] = []
    for item in observed_files:
        match = _SPLIT_GGUF_RE.match(item.intended_filename)
        if not match:
            ordinary.append(item)
            continue
        parent = Path(item.relative_path).parent.as_posix()
        key = (parent, match.group("prefix"), int(match.group("total")))
        split_groups.setdefault(key, []).append((int(match.group("part")), item))

    artifacts = [
        _artifact(
            root,
            identity_path=item.relative_path,
            filename=item.intended_filename,
            relative_path=item.relative_path,
            files=[item],
            incomplete=item.temporary,
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
                split_total=total,
            )
        )

    artifacts.sort(key=lambda item: (item["display_location"].casefold(), item["id"]))
    source["artifact_count"] = len(artifacts)
    return artifacts, source


def inventory_document(
    roots: Iterable[DirectoryRoot],
    *,
    provider_id: str = "directory",
    provider_name: str = "Directory inventory",
) -> dict:
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
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": {"id": provider_id, "name": provider_name},
        "status": {
            "state": state,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sources": sources,
        },
        "artifacts": artifacts,
    }


def _parse_root(value: str, labels: dict[str, str]) -> DirectoryRoot:
    if "=" not in value:
        raise ValueError("--root must use ID=PATH")
    root_id, raw_path = value.split("=", 1)
    root_id = root_id.strip()
    raw_path = raw_path.strip()
    if not raw_path:
        raise ValueError("root path must not be empty")
    return DirectoryRoot(root_id, Path(raw_path).expanduser(), labels.get(root_id, root_id))


def _parse_assignment(value: str, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"{option} must use ID=VALUE")
    key, assigned = value.split("=", 1)
    if not key.strip() or not assigned.strip():
        raise ValueError(f"{option} must use non-empty ID=VALUE")
    return key.strip(), assigned.strip()


def make_handler(roots: list[DirectoryRoot], provider_id: str, provider_name: str, token: str | None):
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
                inventory_document(roots, provider_id=provider_id, provider_name=provider_name),
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
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--provider-id", default="directory")
    parser.add_argument("--provider-name", default="Directory inventory")
    args = parser.parse_args(argv)

    try:
        labels = dict(_parse_assignment(value, "--label") for value in args.label)
        roots = [_parse_root(value, labels) for value in args.root]
    except ValueError as exc:
        parser.error(str(exc))
    token = os.getenv("OUTIS_ARTIFACT_PROVIDER_TOKEN", "").strip() or None
    if not _is_loopback(args.bind) and token is None:
        parser.error("OUTIS_ARTIFACT_PROVIDER_TOKEN is required when binding beyond loopback")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = ThreadingHTTPServer(
        (args.bind, args.port),
        make_handler(roots, args.provider_id, args.provider_name, token),
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
