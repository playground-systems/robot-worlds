"""Package native MJCF without translating its physical or visual model.

Only explicit MJCF asset references are rewritten. All other XML bytes and all
asset/notice bytes are retained. This module never imports a simulator, executes
plugins, fetches dependencies or decides how a scene should be modeled.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.parsers import expat
from xml.sax.saxutils import escape

from .artifacts import canonical, digest, file_digest, inside

_MIB = 1024 * 1024
_MAX_XML = 8 * _MIB
_MAX_FILE = 64 * _MIB
_MAX_TOTAL = 256 * _MIB
_MAX_FILES = 8192
_MAX_REFERENCES = 16384
_MANIFEST = "source-scene.json"
_FILE_ATTRIBUTES = {
    "mesh": {"file"},
    "hfield": {"file"},
    "skin": {"file"},
    "texture": {"file", "fileright", "fileleft", "fileup", "filedown", "filefront", "fileback"},
}


@dataclass(frozen=True)
class SourceSceneBundle:
    directory: Path
    xml_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class _Reference:
    tag: str
    attribute: str
    value: str
    start: int
    end: int
    quote: str


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _attribute_spans(data: bytes, start: int) -> dict[str, tuple[int, int, str]]:
    """Find lexical attribute values in a parser-validated UTF-8 start tag."""
    index = start + 1
    while data[index] not in b" \t\r\n/>":
        index += 1
    spans = {}
    while True:
        while data[index] in b" \t\r\n":
            index += 1
        if data[index] in b"/>":
            return spans
        name_start = index
        while data[index] not in b" \t\r\n=":
            index += 1
        name = data[name_start:index].decode("utf-8")
        while data[index] in b" \t\r\n":
            index += 1
        if data[index] != ord("="):
            raise ValueError("Invalid XML attribute assignment")
        index += 1
        while data[index] in b" \t\r\n":
            index += 1
        quote = data[index]
        if quote not in b"\"'":
            raise ValueError("XML attribute value must be quoted")
        value_start = index + 1
        index = data.index(bytes([quote]), value_start)
        spans[name] = (value_start, index, chr(quote))
        index += 1


def _scan(data: bytes) -> tuple[list[_Reference], dict[str, str]]:
    if len(data) > _MAX_XML:
        raise ValueError("MJCF exceeds the 8 MiB XML budget")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Only UTF-8 MJCF can be packaged without transcoding") from error
    if b"\0" in data:
        raise ValueError("Only UTF-8 MJCF can be packaged without transcoding")
    parser = expat.ParserCreate(encoding="UTF-8")
    references, compiler = [], {}
    root_seen = False
    compiler_seen = False

    def reject(*_):
        raise ValueError("XML declarations/entities and processing instructions are unsupported")

    def declaration(_version, encoding, _standalone):
        if encoding is not None and encoding.lower() not in {"utf-8", "utf8"}:
            raise ValueError("Only UTF-8 MJCF declarations are supported without transcoding")

    def start(tag, attrs):
        nonlocal root_seen, compiler_seen
        if not root_seen:
            if tag != "mujoco":
                raise ValueError("Source scene must be MJCF with a mujoco root")
            root_seen = True
        if ":" in tag or any(":" in key for key in attrs):
            raise ValueError("XML namespaces are unsupported in source MJCF")
        if tag in {"include", "attach", "model", "plugin"} or "plugin" in attrs:
            raise ValueError(
                "External includes, attached models and plugin execution are unsupported"
            )
        if tag == "compiler":
            if compiler_seen:
                raise ValueError("Multiple compiler elements have ambiguous asset lookup semantics")
            compiler_seen = True
            compiler.update(attrs)
        names = [name for name in attrs if name.startswith("file")]
        if names:
            if any(name not in _FILE_ATTRIBUTES.get(tag, ()) for name in names):
                raise ValueError(f"Unsupported MJCF file attribute on {tag}")
            spans = _attribute_spans(data, parser.CurrentByteIndex)
            for name in names:
                begin, end, quote = spans[name]
                references.append(_Reference(tag, name, attrs[name], begin, end, quote))
                if len(references) > _MAX_REFERENCES:
                    raise ValueError("MJCF exceeds the file-reference budget")

    parser.StartElementHandler = start
    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.ProcessingInstructionHandler = reject
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, IndexError) as error:
        raise ValueError("Invalid source MJCF XML") from error
    if compiler.get("strippath", "false") != "false":
        raise ValueError("compiler strippath must be false for lossless portable file references")
    for name in ("assetdir", "meshdir", "texturedir"):
        _relative_member(compiler.get(name, "."), allow_dot=True)
    return sorted(references, key=lambda reference: reference.start), compiler


def _relative_member(value: str, *, allow_dot=False) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("File paths must be local, unambiguous POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or (not allow_dot and path == PurePosixPath(".")):
        raise ValueError("File path escapes its source or bundle")
    return path


def _asset_directory(reference: _Reference, compiler: dict[str, str]) -> PurePosixPath:
    setting = "texturedir" if reference.tag == "texture" else "meshdir"
    return _relative_member(compiler.get(setting, compiler.get("assetdir", ".")), allow_dot=True)


def _bounded_file(root: Path, value: str, *, base: Path | None = None) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Source file must be a local path without a resource-provider URI")
    candidate = ((base or root) / value).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError(f"Missing source file or path outside source root: {value}")
    if candidate.stat().st_size > _MAX_FILE:
        raise ValueError("Source dependency exceeds the 64 MiB file budget")
    return candidate


def _masked(data: bytes, references: list[_Reference]) -> bytes:
    for reference in reversed(references):
        data = data[: reference.start] + b"__MJCF_FILE__" + data[reference.end :]
    return data


def _rewrite(data: bytes, references: list[_Reference], values: list[str]) -> bytes:
    for reference, value in reversed(list(zip(references, values, strict=True))):
        encoded = escape(value, {reference.quote: "&quot;" if reference.quote == '"' else "&apos;"})
        data = data[: reference.start] + encoded.encode("utf-8") + data[reference.end :]
    return data


def package_source_scene(
    xml_path: str | Path,
    source_root: str | Path,
    output_dir: str | Path,
    provenance: Mapping[str, Any],
) -> SourceSceneBundle:
    """Copy a bounded native MJCF closure, changing only file-attribute values.

    ``provenance`` must be JSON data with a nonempty ``licenses`` list. Each
    license is a mapping containing ``path`` (a source-root-relative retained
    notice) and ``license`` (its caller-supplied declaration). Additional source
    URLs, revisions, attribution and audit metadata are preserved verbatim.

    Includes, attached models, plugins, path-stripping and absolute/escaping
    compiler directories are rejected. The closure is MJCF's explicit native
    mesh/heightfield/skin/texture files; it does not reinterpret OBJ materials or
    run resource providers. No native compatibility/physics claim is made.
    """
    root, destination = Path(source_root).resolve(), Path(output_dir).resolve()
    if not root.is_dir():
        raise ValueError("Source root must be an existing directory")
    if destination.exists() or Path(output_dir).is_symlink():
        raise ValueError("Output directory already exists; source bundles are immutable")
    source_xml = _bounded_file(root, str(Path(xml_path).resolve()))
    original = source_xml.read_bytes()
    references, compiler = _scan(original)
    try:
        evidence = json.loads(canonical(dict(provenance)))
    except (TypeError, ValueError) as error:
        raise ValueError("Source provenance must be finite JSON data") from error
    licenses = evidence.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise ValueError("Source provenance requires retained licenses")

    sources: dict[str, tuple[Path, str]] = {}

    def add(member: PurePosixPath, source: Path, kind: str):
        key = member.as_posix()
        if key in sources and sources[key][0] != source:
            raise ValueError("Bundled source filenames collide")
        sources[key] = (source, kind)
        if len(sources) > _MAX_FILES:
            raise ValueError("Source scene exceeds the file-count budget")

    rows, values = [], []
    for reference in references:
        directory = _asset_directory(reference, compiler)
        source = _bounded_file(root, reference.value, base=source_xml.parent / directory)
        relative = source.relative_to(root).as_posix()
        value = PurePosixPath("assets") / relative
        member = directory / value
        add(member, source, "asset")
        values.append(value.as_posix())
        rows.append(
            dict(
                tag=reference.tag,
                attribute=reference.attribute,
                original_value=reference.value,
                bundled_value=value.as_posix(),
                source_path=relative,
                bundle_path=member.as_posix(),
            )
        )
    for notice in licenses:
        if (
            not isinstance(notice, dict)
            or not isinstance(notice.get("license"), str)
            or not notice["license"]
        ):
            raise ValueError("Every retained license needs a declaration and source-relative path")
        member = _relative_member(notice.get("path"))
        source = _bounded_file(root, member.as_posix())
        add(PurePosixPath("notices") / member, source, "license")
    original_member = PurePosixPath("original") / source_xml.relative_to(root).as_posix()
    add(original_member, source_xml, "original_xml")
    bundled = _rewrite(original, references, values)
    bundled_references, _ = _scan(bundled)
    if _masked(original, references) != _masked(bundled, bundled_references):
        raise ValueError("Unexpected change outside MJCF file references")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".source-scene-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        files, total = {}, len(bundled)
        for member, (source, kind) in sorted(sources.items()):
            expected = file_digest(source)
            target = stage / member
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            actual, size = file_digest(target), target.stat().st_size
            if actual != expected or (kind == "original_xml" and actual != _hash(original)):
                raise ValueError("Original source changed during packaging")
            total += size
            if total > _MAX_TOTAL:
                raise ValueError("Source scene exceeds the 256 MiB bundle budget")
            files[member] = dict(
                source_path=source.relative_to(root).as_posix(),
                original_sha256=expected,
                bundled_sha256=actual,
                bytes=size,
                kind=kind,
            )
        scene_member = source_xml.name
        (stage / scene_member).write_bytes(bundled)
        payload = dict(
            schema_version=1,
            format="robot-worlds-native-source-scene",
            scene=dict(
                source_path=source_xml.relative_to(root).as_posix(),
                original_path=original_member.as_posix(),
                bundle_path=scene_member,
                original_sha256=_hash(original),
                bundled_sha256=_hash(bundled),
            ),
            files=files,
            references=rows,
            provenance=evidence,
            validation=dict(
                xml_preserved_except_file_attributes=True,
                native_compile_performed=False,
                physics_verified=False,
            ),
        )
        manifest = {**payload, "id": digest(payload)}
        manifest_data = json.dumps(manifest, indent=2, allow_nan=False).encode() + b"\n"
        if len(manifest_data) > _MAX_XML:
            raise ValueError("Source scene manifest exceeds 8 MiB")
        if total + len(manifest_data) > _MAX_TOTAL:
            raise ValueError("Source scene exceeds the 256 MiB bundle budget")
        (stage / _MANIFEST).write_bytes(manifest_data)
        verify_source_scene_bundle(stage)
        stage.rename(destination)
    return SourceSceneBundle(
        destination, destination / scene_member, destination / _MANIFEST, manifest
    )


def verify_source_scene_bundle(path: str | Path) -> SourceSceneBundle:
    """Verify portable hashes, bounded references and the exact XML-only rewrite.

    Manifest identities detect accidental alteration; they are not signatures.
    Verification never consults or requires the original source directory.
    """
    root = Path(path).resolve()
    manifest_path = inside(root, _MANIFEST)
    if not manifest_path.is_file() or manifest_path.stat().st_size > _MAX_XML:
        raise ValueError("Source scene needs a manifest of at most 8 MiB")
    try:
        manifest = json.loads(manifest_path.read_text())
        payload = {key: value for key, value in manifest.items() if key != "id"}
        if manifest.get("id") != digest(payload):
            raise ValueError("Source scene manifest integrity check failed")
        if (
            manifest["schema_version"] != 1
            or manifest["format"] != "robot-worlds-native-source-scene"
        ):
            raise ValueError("Unsupported source scene manifest")
        files = manifest["files"]
        if not isinstance(files, dict) or len(files) > _MAX_FILES:
            raise ValueError("Invalid source scene file inventory")
        total = 0
        for member, row in files.items():
            _relative_member(member)
            target = inside(root, member)
            if not target.is_file() or target.stat().st_size > _MAX_FILE:
                raise ValueError("Source scene dependency is missing or exceeds file budget")
            total += target.stat().st_size
            if total > _MAX_TOTAL:
                raise ValueError("Source scene exceeds its bundle budget")
            if (
                row["original_sha256"] != row["bundled_sha256"]
                or file_digest(target) != row["bundled_sha256"]
                or target.stat().st_size != row["bytes"]
            ):
                raise ValueError("Source scene dependency integrity check failed")
        scene = manifest["scene"]
        _relative_member(scene["bundle_path"])
        _relative_member(scene["original_path"])
        xml_path = inside(root, scene["bundle_path"])
        original_path = inside(root, scene["original_path"])
        if scene["original_path"] not in files or xml_path.stat().st_size > _MAX_XML:
            raise ValueError("Source scene XML is missing or exceeds XML budget")
        original, bundled = original_path.read_bytes(), xml_path.read_bytes()
        if total + len(bundled) + manifest_path.stat().st_size > _MAX_TOTAL:
            raise ValueError("Source scene exceeds its bundle budget")
        if _hash(original) != scene["original_sha256"] or _hash(bundled) != scene["bundled_sha256"]:
            raise ValueError("Source scene XML integrity check failed")
        original_refs, _ = _scan(original)
        bundle_refs, compiler = _scan(bundled)
        if _masked(original, original_refs) != _masked(bundled, bundle_refs):
            raise ValueError("Source scene XML changed outside file references")
        rows = manifest["references"]
        if len(rows) != len(bundle_refs) or len(original_refs) != len(bundle_refs):
            raise ValueError("Source scene reference inventory mismatch")
        for previous, current, row in zip(original_refs, bundle_refs, rows, strict=True):
            if (previous.tag, previous.attribute, previous.value, current.value) != (
                row["tag"],
                row["attribute"],
                row["original_value"],
                row["bundled_value"],
            ):
                raise ValueError("Source scene file-reference rewrite mismatch")
            relative = _relative_member(current.value)
            member = (_asset_directory(current, compiler) / relative).as_posix()
            if member != row["bundle_path"] or member not in files:
                raise ValueError("Source scene reference is missing from its file inventory")
            inside(root, member)
        licenses = manifest["provenance"]["licenses"]
        if not isinstance(licenses, list) or not licenses:
            raise ValueError("Source scene is missing retained licenses")
        for notice in licenses:
            member = (PurePosixPath("notices") / _relative_member(notice["path"])).as_posix()
            if member not in files or files[member]["kind"] != "license" or not notice["license"]:
                raise ValueError("Source scene license is missing from its file inventory")
    except (KeyError, TypeError, AttributeError, OSError, json.JSONDecodeError) as error:
        raise ValueError("Invalid or incomplete source scene bundle") from error
    return SourceSceneBundle(root, xml_path, manifest_path, manifest)
