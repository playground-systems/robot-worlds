"""Restricted SDF scanned-mesh import; source failures never authorize creation.

One model/link with matching OBJ visual and collision meshes. Original files are
retained; concave sources require an existing verified collision decomposition.
This does not flatten articulated models or execute Gazebo plugins.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np
from pydantic import TypeAdapter

from .artifacts import digest
from .asset_download import DownloadedAsset, _cached, _identity
from .asset_import import ImportedAsset, import_convex_mesh_asset
from .asset_sourcing import AssetCandidate
from .mesh_assets import validate_texture, verified_convex_obj
from .models import Identifier, SceneObject

_VERSIONS = {f"1.{minor}" for minor in range(6, 13)}


@dataclass
class _Source:
    root: Path
    sdf: Path
    model_name: str
    link_name: str
    collision_name: str
    mesh: Path
    texture: Path | None
    color: tuple
    opacity: float
    static: bool
    offset: np.ndarray
    files: dict
    notices: list[str]


def _bytes(path, maximum):
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"Missing or oversized SDF source file: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("SDF source file exceeds its read budget")
    return data


def _xml(path):
    data = _bytes(path, 2 * 1024 * 1024)
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
        raise ValueError("SDF declarations/entities are unsupported")
    # Preserve copyright/license comments for the generated source notice.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.fromstring(data, parser=parser), data


def _comments(data):
    # ElementTree discards comments before the root, where copyright notices
    # commonly live. The XML was parsed first, so malformed comments already fail.
    return [match.decode("utf-8").strip() for match in re.findall(br"<!--(.*?)-->", data, re.DOTALL)]


def _children(node, allowed, attributes=(), repeated=()):
    if set(node.attrib) - set(attributes):
        raise ValueError(f"Unsupported SDF {node.tag} attributes")
    seen = set()
    for child in node:
        if not isinstance(child.tag, str):
            continue
        if child.tag not in allowed or child.tag in seen and child.tag not in repeated:
            raise ValueError(f"Unsupported or duplicate SDF element: {child.tag}")
        seen.add(child.tag)


def _text(node, name, default=None):
    element = node.find(name)
    if element is None:
        return default
    _children(element, ())
    value = (element.text or "").strip()
    if not value:
        raise ValueError(f"Empty SDF {name}")
    return value


def _boolean(node, name, default):
    value = _text(node, name, "true" if default else "false")
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"Invalid SDF boolean: {name}")
    return value in {"true", "1"}


def _vector(text, count):
    values = np.asarray([float(value) for value in text.split()])
    if values.shape != (count,) or not np.isfinite(values).all():
        raise ValueError("Invalid SDF vector")
    return values


def _pose(node):
    pose = _vector(_text(node, "pose", "0 0 0 0 0 0"), 6)
    if np.any(np.abs(pose[3:]) > 1e-12):
        raise ValueError("Rotated SDF poses require a supported rotation importer")
    return pose[:3]


def _path(value, root, base, model_name=None):
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment or "\\" in value or "\x00" in value:
        raise ValueError("Unsupported SDF resource URI")
    if parsed.scheme == "model" and model_name is not None:
        if unquote(parsed.netloc) != model_name:
            raise ValueError("External SDF model references are unsupported")
        value, base = unquote(parsed.path.lstrip("/"), errors="strict"), root
    elif parsed.scheme or parsed.netloc:
        raise ValueError("Remote/absolute SDF resources are unsupported")
    else:
        value = unquote(value, errors="strict")
    if not value or Path(value).is_absolute() or "\\" in value or "\x00" in value:
        raise ValueError("Unsupported SDF resource path")
    path = (base / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("SDF resource escapes its model package")
    return path


def _mesh(node, root, base, name):
    geometry = node.find("geometry")
    if geometry is None:
        raise ValueError("SDF needs explicit mesh geometry")
    _children(geometry, {"mesh"})
    mesh = geometry.find("mesh")
    if mesh is None:
        raise ValueError("This SDF importer supports scanned OBJ meshes only")
    _children(mesh, {"uri", "scale"})
    if not np.array_equal(_vector(_text(mesh, "scale", "1 1 1"), 3), np.ones(3)):
        raise ValueError("Scaled SDF meshes require a separately prepared source")
    uri = _text(mesh, "uri")
    if uri is None:
        raise ValueError("SDF mesh has no URI")
    path = _path(uri, root, base, name)
    if path.suffix.lower() != ".obj":
        raise ValueError("SDF scan import currently requires OBJ geometry")
    _bytes(path, 24 * 1024 * 1024)
    return path


def _inspect(path):
    path = Path(path).resolve()
    notices, files, expected_version = [], {}, None
    root = path if path.is_dir() else path.parent
    package_root = root
    config = _path("model.config", root, root)
    if path.is_dir() and not config.is_file():
        configs = list(root.glob("*/model.config"))
        if len(configs) != 1:
            raise ValueError("Fuel package must have one unambiguous model root")
        root, config = configs[0].parent.resolve(), configs[0].resolve()
        if not root.is_relative_to(package_root) or not config.is_relative_to(root):
            raise ValueError("Model root escapes its source package")
    if config.is_file():
        tree, data = _xml(config)
        if tree.tag != "model":
            raise ValueError("Invalid model.config root")
        files[str(config)] = hashlib.sha256(data).hexdigest()
        notices.extend(_comments(data))
        notices.extend("Source author: " + (node.text or "").strip() for node in tree.findall("author/name"))
        if path.is_dir():
            entries = tree.findall("sdf")
            if len(entries) != 1 or not (entries[0].text or "").strip():
                raise ValueError("Choose an explicit SDF file when model.config has multiple versions")
            expected_version = entries[0].get("version")
            path = _path(entries[0].text.strip(), root, root)
    elif path.is_dir():
        raise ValueError("Fuel package has no model.config")
    tree, data = _xml(path)
    _children(tree, {"model"}, {"version"})
    if tree.tag != "sdf" or tree.get("version") not in _VERSIONS or tree.find("model") is None:
        raise ValueError("Expected one SDF 1.6–1.12 model")
    if expected_version is not None and expected_version != tree.get("version"):
        raise ValueError("model.config and SDF versions disagree")
    files[str(path)] = hashlib.sha256(data).hexdigest()
    notices.extend(_comments(data))
    model = tree.find("model")
    _children(model, {"static", "pose", "link"}, {"name"})
    name = model.get("name")
    link = model.find("link")
    if not name or link is None or not link.get("name"):
        raise ValueError("SDF needs a named model and a named link")
    _children(link, {"pose", "visual", "collision", "gravity", "kinematic", "enable_wind", "self_collide"}, {"name"})
    if (not _boolean(link, "gravity", True) or _boolean(link, "kinematic", False)
            or _boolean(link, "enable_wind", False) or _boolean(link, "self_collide", False)):
        raise ValueError("Unsupported SDF link dynamics")
    visual, collision = link.find("visual"), link.find("collision")
    if visual is None or collision is None or not collision.get("name") or not visual.get("name"):
        raise ValueError("SDF scan needs one named visual and one named collision")
    _children(visual, {"geometry", "pose", "transparency", "cast_shadows"}, {"name"})
    _children(collision, {"geometry", "pose"}, {"name"})
    if not _boolean(visual, "cast_shadows", True):
        raise ValueError("Source-disabled shadow casting is unsupported")
    transparency = float(_text(visual, "transparency", "0"))
    if not math.isfinite(transparency) or not 0 <= transparency <= 1:
        raise ValueError("Invalid SDF transparency")
    mesh = _mesh(visual, root, path.parent, name)
    if _mesh(collision, root, path.parent, name) != mesh or not np.array_equal(_pose(visual), _pose(collision)):
        raise ValueError("SDF scan visual and collision sources/poses must match")
    offset = _pose(model) + _pose(link) + _pose(visual)
    texture, color, opacity = _texture(mesh, root, files, notices)
    notice_names = {name + suffix for name in ("license", "licence", "notice", "copyright", "attribution")
                    for suffix in ("", ".md", ".txt")}
    notice_names.add("metadata.pbtxt")
    for extra in sorted(root.iterdir()):
        if extra.name.lower() in notice_names:
            extra = _path(extra.name, root, root)
            content = _bytes(extra, 1024 * 1024)
            files[str(extra)] = hashlib.sha256(content).hexdigest()
            notices.append(f"Retained {extra.name}:\n" + content.decode("utf-8"))
    return _Source(root, path, name, link.get("name"), collision.get("name"), mesh, texture,
                   color, opacity*(1-transparency), _boolean(model, "static", False), offset, files, notices)


def _texture(mesh, root, files, notices):
    data = _bytes(mesh, 24 * 1024 * 1024)
    files[str(mesh)] = hashlib.sha256(data).hexdigest()
    declarations, materials = [], set()
    for line in data.decode().splitlines():
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] in {"mtllib", "usemtl"}:
            if len(fields) != 2:
                raise ValueError("Only single-material OBJ resources are supported")
            if fields[0] == "mtllib":
                declarations.append(fields[1])
            else:
                materials.add(fields[1])
    if not declarations and not materials:
        return None, (1., 1., 1.), 1.
    if len(declarations) != 1 or len(materials) > 1:
        raise ValueError("Only one OBJ material library/material is supported")
    material = _path(declarations[0], root, mesh.parent)
    data = _bytes(material, 1024 * 1024)
    files[str(material)] = hashlib.sha256(data).hexdigest()
    names, texture, color, opacity = [], None, (1., 1., 1.), 1.
    seen = set()
    for line in data.decode().splitlines():
        if line.lstrip().startswith("#"):
            notices.append(line.lstrip()[1:].strip())
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        key, values = fields[0], fields[1:]
        if key in seen:
            raise ValueError("Repeated MTL settings need a supported material importer")
        seen.add(key)
        if key == "newmtl" and len(values) == 1:
            names.append(values[0])
        elif key == "map_Kd" and len(values) == 1:
            texture = _path(values[0], root, material.parent)
            if not texture.is_file():
                # Google Scanned Objects places its simple map_Kd filename here.
                texture = _path(values[0], root, root / "materials/textures")
            validate_texture(texture)
            files[str(texture)] = hashlib.sha256(texture.read_bytes()).hexdigest()
        elif key == "Kd":
            color = tuple(_vector(" ".join(values), 3))
            if any(value < 0 or value > 1 for value in color):
                raise ValueError("MTL diffuse color must be in [0,1]")
        elif key in {"d", "Tr"} and len(values) == 1:
            if "d" in seen and "Tr" in seen:
                raise ValueError("Ambiguous MTL opacity")
            opacity = float(values[0]) if key == "d" else 1-float(values[0])
            if not math.isfinite(opacity) or not 0 <= opacity <= 1:
                raise ValueError("Invalid MTL opacity")
        elif key in {"Ka", "Ks", "Ns", "Ni", "illum"}:
            notices.append(f"Source material setting retained but not reproduced by the renderer: {line}")
        else:
            raise ValueError(f"Unsupported MTL setting: {key}")
    if len(names) != 1 or materials and materials != set(names):
        raise ValueError("OBJ material reference is ambiguous or missing")
    return texture, color, opacity


def _import(source, *, prefix, mass, source_notice, collision_manifest, position, friction):
    TypeAdapter(Identifier).validate_python(prefix)
    if isinstance(mass, bool) or not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0:
        raise ValueError("SDF mesh import requires an explicit finite positive estimated mass")
    if isinstance(friction, bool) or not isinstance(friction, (int, float)) or not math.isfinite(friction) or friction < 0:
        raise ValueError("SDF mesh import requires finite nonnegative estimated friction")
    offset = _vector(" ".join(map(str, position)), 3) + source.offset
    notice = Path(source_notice).resolve()
    if notice.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("SDF import requires a retained text attribution notice")
    _bytes(notice, 1024 * 1024)
    if collision_manifest is not None:
        asset = import_convex_mesh_asset(source.mesh, collision_manifest, prefix=prefix, mass=mass,
                                         source_notice=notice, texture_path=source.texture, position=offset,
                                         movable=not source.static, friction=friction)
    else:
        try:
            lower, upper, _ = verified_convex_obj(source.mesh, source.files[str(source.mesh)])
        except ValueError as exc:
            raise ValueError("Source mesh needs a verified collision decomposition; it cannot be replaced by its convex hull") from exc
        root = prefix + "_body"
        center, size = offset+(lower+upper)/2, upper-lower
        asset = ImportedAsset(objects=[
            SceneObject(id=root, label=source.model_name, kind="mesh", mesh_path=str(source.mesh),
                        position=center, size=size, movable=not source.static, mass=mass, friction=friction,
                        visible=False, source_notice=str(notice)),
            SceneObject(id=prefix+"_visual", label=source.model_name, kind="mesh", mesh_path=str(source.mesh),
                        position=center, size=size, parent_id=root, collision=False,
                        texture_path=str(source.texture) if source.texture else None, source_notice=str(notice)),
        ], body_ids={"object": root}, disclosures=["Source collision mesh was verified closed and convex; no convex-hull substitution was used."])
    visual = next(obj for obj in asset.objects if not obj.collision)
    visual.color, visual.opacity = source.color, source.opacity
    root = asset.body_ids["object"]
    asset.body_ids[source.link_name] = root
    asset.geom_ids[source.collision_name] = root
    asset.source_files.update({str(Path(path).relative_to(source.root)): value for path, value in source.files.items()})
    asset.source_files["attribution/"+notice.name] = hashlib.sha256(notice.read_bytes()).hexdigest()
    asset.disclosures.extend([
        "Restricted SDF scanned-mesh import; original OBJ/UV/texture bytes are retained.",
        f"Source static={str(source.static).lower()} is preserved. Mass {mass:g} kg and friction {friction:g} are caller-supplied estimates; inertia is derived from collision geometry.",
        "Renderer lighting/material response is approximate; source MTL settings and notices are retained.",
    ])
    return asset


def import_sdf_mesh_asset(path: str | Path, *, prefix: str, mass: float, source_notice: str | Path,
                          collision_manifest: str | Path | None = None, position=(0, 0, 0), friction=.7):
    """Import a single-link OBJ scan from SDF or a model.config package.

    Only translations, unit mesh scale, one material/PNG, and matching visual/
    collision meshes are supported. Articulation, explicit inertial/surface data,
    remote resources, plugins and unsupported settings fail explicitly. Without
    a decomposition, the source must pass the closed-convex verifier.
    """
    return _import(_inspect(path), prefix=prefix, mass=mass, source_notice=source_notice,
                   collision_manifest=collision_manifest, position=position, friction=friction)


def import_fuel_mesh_asset(downloaded: DownloadedAsset, *, candidate: AssetCandidate, prefix: str,
                           mass: float, collision_manifest: str | Path | None = None,
                           position=(0, 0, 0), friction=.7):
    """Import a downloaded scan without modifying its cached source package.

    Candidate evidence must match the pinned download. Derived attribution and
    import metadata live beside the source, outside its verified byte inventory.
    Use this adapter inside source_asset before registering an AssetLibrary entry.
    """
    checked = _cached(downloaded.manifest_path.parent, _identity(candidate), time.monotonic()+60)
    if checked.source_dir.resolve() != downloaded.source_dir.resolve() or checked.archive_sha256 != downloaded.archive_sha256:
        raise ValueError("Downloaded source identity changed before import")
    source = _inspect(checked.source_dir)
    notices = list(dict.fromkeys(text for text in source.notices if text))
    text = (f"# Source asset attribution\n\n{candidate.id}\n\nSource: {candidate.source_url}\n"
            f"Revision: {candidate.revision}\nLicense: {candidate.license}\nLicense URL: {candidate.metadata['license_url']}\n"
            f"Source ZIP SHA-256: {checked.archive_sha256}\n\n" + "\n\n".join(notices) +
            "\n\nRobot Worlds retains source geometry/UVs/textures. Collision conversion, estimated mass/friction and derived inertia are disclosed in the imported world.\n")
    if len(text.encode()) > 1024 * 1024:
        raise ValueError("Retained source attribution exceeds 1 MiB")
    metadata = {"schema_version": 1, "source": asdict(candidate), "archive_sha256": checked.archive_sha256,
                "source_files": json.loads(checked.manifest_path.read_text())["files"],
                "source_static": source.static, "mass_estimate_kg": mass, "friction_estimate": friction}
    if collision_manifest is not None:
        metadata["collision_conversion"] = json.loads(_bytes(Path(collision_manifest), 2 * 1024 * 1024))
    encoded = json.dumps(metadata, indent=2) + "\n"
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError("Import provenance exceeds 2 MiB")
    destination = (checked.manifest_path.parent / "imports" / digest({"notice": text, "metadata": metadata})).resolve()
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix=".sdf-import-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "record"
            stage.mkdir()
            (stage / "ATTRIBUTION.md").write_text(text)
            (stage / "import.json").write_text(encoded)
            try:
                stage.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
    if (destination / "ATTRIBUTION.md").read_text() != text or (destination / "import.json").read_text() != encoded:
        raise ValueError("Derived source provenance was modified")
    asset = _import(source, prefix=prefix, mass=mass, source_notice=destination / "ATTRIBUTION.md",
                    collision_manifest=collision_manifest, position=position, friction=friction)
    for obj in asset.objects:
        obj.source_metadata = str(destination / "import.json")
    asset.source_files["import/provenance.json"] = hashlib.sha256(encoded.encode()).hexdigest()
    return asset
