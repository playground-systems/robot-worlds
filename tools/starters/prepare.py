"""Offline release preparation for four pinned AWS scene-data imports.

Run on a hosted worker, never during a customer click. No ROS packages, Gazebo
plugins, source launch scripts or model inference are executed. Output is data.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import tarfile
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import collada
import httpx
import numpy as np
from fuel import acquire_hospital_fuel
from geometry import prepare_static_collision, prepare_static_envelopes, thicken_planar_visual
from PIL import Image

from robot_worlds.models import WorldSpec

CATALOG = Path(__file__).resolve().parents[2] / "src/robot_worlds/assets/starters/catalog.json"


def xml(path):
    data = path.read_bytes()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entity declarations are not scene data")
    return ET.fromstring(data)


def pose(node):
    values = np.fromstring(node.findtext("pose", "0 0 0 0 0 0"), sep=" ")
    if values.shape != (6,) or not np.isfinite(values).all():
        raise ValueError("Invalid source pose")
    x, y, z, roll, pitch, yaw = values
    cr, sr, cp, sp, cy, sy = (
        math.cos(roll),
        math.sin(roll),
        math.cos(pitch),
        math.sin(pitch),
        math.cos(yaw),
        math.sin(yaw),
    )
    matrix = np.eye(4)
    matrix[:3, :3] = (
        np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
        @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
        @ np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    )
    matrix[:3, 3] = [x, y, z]
    return matrix


def resolve(uri, source, base, disclosures=None):
    uri = unquote(uri)
    if uri.startswith("model://"):
        path = source / "models" / uri.removeprefix("model://")
    elif uri.startswith("file://"):
        path = source / uri.removeprefix("file://")
    else:
        path = base / uri
    path = path.resolve()
    if path.is_relative_to(source.resolve()) and path.is_file():
        return path
    # Reviewed source-exporter quirks: portrait images live in the repository's
    # photos directory, and Spotlight's SDF disagrees with its filename's case.
    candidate = None
    if uri.startswith("../../../../photos/") and len(Path(uri).parts) == 6:
        candidate = source / "photos" / Path(uri).name
    elif path.parent.is_relative_to(source.resolve()) and path.parent.is_dir():
        matches = [
            p
            for p in path.parent.iterdir()
            if p.name.casefold() == path.name.casefold() and p.is_file()
        ]
        if len(matches) == 1:
            candidate = matches[0]
    if (
        candidate is None
        or not candidate.is_file()
        or not candidate.resolve().is_relative_to(source.resolve())
    ):
        raise ValueError(f"Unresolved source resource: {uri}")
    if disclosures is not None:
        disclosures.append(
            f"Source URI {uri} resolved to the original file {candidate.relative_to(source)} (exporter path/case correction)"
        )
    return candidate.resolve()


def acquire(entry, cache):
    target = cache / entry["id"]
    if (target / "LICENSE").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    url = f"https://codeload.github.com/{entry['repo']}/tar.gz/{entry['revision']}"
    archive = cache / (entry["id"] + ".tar.gz")
    for attempt in range(3):
        try:
            count = 0
            with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        count += len(chunk)
                        if count > 160 * 1024 * 1024:
                            raise ValueError("Source archive exceeds acquisition budget")
                        output.write(chunk)
            break
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(2)
    total = 0
    with tarfile.open(archive) as bundle:
        for member in bundle:
            relative = Path(*Path(member.name).parts[1:])
            if not relative.parts or relative.parts[0] not in {
                "models",
                "worlds",
                "photos",
                "LICENSE",
            }:
                continue
            if member.issym() or member.islnk() or ".." in relative.parts or relative.is_absolute():
                raise ValueError("Unsafe source archive member")
            if member.isfile():
                total += member.size
                if total > 250 * 1024 * 1024 or member.size > 32 * 1024 * 1024:
                    raise ValueError("Expanded source exceeds budget")
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.extractfile(member).read())
    (target / "acquisition.json").write_text(
        json.dumps(
            {
                "url": url,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "bytes": archive.stat().st_size,
            }
        )
    )
    return target


def primitives(path, disclosures=None, *, root=None):
    if path.suffix.lower() == ".obj":
        from obj_source import read_obj

        for primitive in read_obj(path, root=root or path.parent):
            yield primitive, 1.0
        return
    if path.suffix.lower() != ".dae":
        raise ValueError(f"Unreviewed source mesh format: {path.suffix}")
    document = collada.Collada(str(path))
    if document.assetInfo.upaxis not in (None, "Z_UP", "Y_UP"):
        raise ValueError(f"Unreviewed source up-axis: {path}")
    # These are Gazebo scene imports, not a general-purpose COLLADA viewer.
    # Gazebo Classic's loader applies authored scene-node transforms and unit
    # scale, but does not apply the asset.up_axis metadata. Hospital exports
    # declare Y_UP and already contain the rotations producing Z-up geometry.
    # Rotating the bound vertices again would turn their floors into walls.
    # https://github.com/gazebosim/gazebo-classic/blob/gazebo11/gazebo/common/ColladaLoader.cc
    if document.assetInfo.upaxis == "Y_UP" and disclosures is not None:
        disclosures.append(
            f"{path.name}: Y_UP metadata retained with authored scene-node rotations "
            "and metre unit conversion, matching Gazebo Classic; no extra up-axis rotation"
        )
    unit = document.assetInfo.unitmeter
    if unit is None:
        unit = 1.0
    if not math.isfinite(unit) or not 0.0001 <= unit <= 100:
        raise ValueError("Invalid COLLADA unit scale")
    if document.scene is None:
        raise ValueError("COLLADA scene is missing")
    for geometry in document.scene.objects("geometry"):
        for primitive in geometry.primitives():
            if hasattr(primitive, "triangleset"):
                primitive = primitive.triangleset()
            if not hasattr(primitive, "vertex_index"):
                raise ValueError(f"Unsupported COLLADA primitive in {path}")
            vertices, indices = primitive.vertex, primitive.vertex_index
            if len(primitive) == 0:
                # Some original AWS exports contain an unused material group:
                # <triangles count="0"><p/></triangles>. Pycollada correctly
                # exposes it with no position/index arrays. Verify that exact
                # source condition; a nonempty unsupported group must fail.
                original = getattr(primitive, "original", primitive)
                node = getattr(original, "xmlnode", None)
                empty = (
                    node is not None
                    and node.tag.rsplit("}", 1)[-1] == "triangles"
                    and node.get("count") == "0"
                    and all(
                        not (child.text or "").strip()
                        for child in node
                        if child.tag.rsplit("}", 1)[-1] == "p"
                    )
                    and (indices is None or not np.asarray(indices).size)
                )
                if not empty:
                    raise ValueError(f"Unreviewed empty COLLADA primitive in {path}")
                if disclosures is not None:
                    disclosures.append(
                        f"{path.name}: skipped explicitly empty COLLADA triangle material group "
                        "(source count=0, no face indices); no source surface removed"
                    )
                continue
            if (
                vertices is None
                or indices is None
                or np.asarray(indices).ndim != 2
                or np.asarray(indices).shape[1] != 3
            ):
                raise ValueError(f"Unsupported or incomplete COLLADA triangles in {path}")
            yield primitive, unit


def obj_bytes(vertices, faces, uv=None, normals=None):
    # OBJ's independent indices retain UV/normal seams without tripling positions.
    text = "".join("v " + " ".join(f"{x:.8g}" for x in row) + "\n" for row in vertices)
    if uv is not None:
        text += "".join(
            "vt " + " ".join(f"{x:.8g}" for x in row) + "\n" for row in uv.reshape(-1, 2)
        )
    if normals is not None:
        text += "".join(
            "vn " + " ".join(f"{x:.8g}" for x in row) + "\n" for row in normals.reshape(-1, 3)
        )

    def ref(vertex, corner):
        return (
            str(vertex + 1)
            + (f"/{corner + 1}" if uv is not None else "/" if normals is not None else "")
            + (f"/{corner + 1}" if normals is not None else "")
        )

    text += "".join(
        "f " + " ".join(ref(vertex, i * 3 + j) for j, vertex in enumerate(face)) + "\n"
        for i, face in enumerate(faces)
    )
    return text.encode()


class Adapter:
    def __init__(self, entry, source, output, cache):
        self.entry, self.source, self.output, self.cache = entry, source, output, cache
        self.files = output / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.objects, self.inputs, self.disclosures = [], {}, []
        self.collision_cache = {}
        self.texture_cache = {}
        external = source / "fuel-sources.json"
        self.external_sources = json.loads(external.read_text()) if external.exists() else []
        self.external_models = {record["name"] for record in self.external_sources}

    def store(self, data, suffix):
        name = hashlib.sha256(data).hexdigest() + suffix
        path = self.files / name
        if not path.exists():
            path.write_bytes(data)
        return "files/" + name

    def input(self, path):
        relative = str(path.relative_to(self.source))
        if relative not in self.inputs:
            self.inputs[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    def geometry(self, node):
        mesh = node.find("geometry/mesh")
        if mesh is None:
            raise ValueError("Starter source currently requires reviewed mesh geometry")
        path = resolve(mesh.findtext("uri"), self.source, self.source, self.disclosures)
        self.input(path)
        scale = np.fromstring(mesh.findtext("scale", "1 1 1"), sep=" ")
        if scale.shape != (3,) or np.any(scale <= 0):
            raise ValueError("Invalid source mesh scale")
        return path, scale

    def texture(self, primitive, mesh):
        color, texture, opacity = [0.65, 0.65, 0.65], None, 1.0
        path = None
        if hasattr(primitive, "source_color"):
            color, opacity = list(primitive.source_color[:3]), float(primitive.source_color[3])
            path = primitive.source_texture_path
            for resource in primitive.source_material_files:
                self.input(resource)
        elif primitive.material is not None:
            diffuse = primitive.material.effect.diffuse
            if isinstance(diffuse, collada.material.Map):
                path = resolve(
                    diffuse.sampler.surface.image.path, self.source, mesh.parent, self.disclosures
                )
                color = [1, 1, 1]
            elif diffuse is not None:
                color = list(diffuse[:3])
                opacity = float(diffuse[3]) if len(diffuse) == 4 else 1.0
        if path is not None:
            self.input(path)
            if str(path) not in self.texture_cache:
                with Image.open(path) as image:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    data = io.BytesIO()
                    image.save(data, format="PNG", optimize=True)
                self.texture_cache[str(path)] = self.store(data.getvalue(), ".png")
            texture = self.texture_cache[str(path)]
        return color, texture, opacity

    def add_mesh(
        self,
        vertices,
        faces,
        transform,
        label,
        *,
        physical=False,
        parent=None,
        uv=None,
        normals=None,
        material=None,
    ):
        used, inverse = np.unique(np.asarray(faces).reshape(-1), return_inverse=True)
        vertices, faces = vertices[used], inverse.reshape(-1, 3)
        transformed = vertices @ transform[:3, :3].T + transform[:3, 3]
        if normals is not None:
            normals = normals @ np.linalg.inv(transform[:3, :3])
            normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-12)
        if not physical:
            transformed, faces, uv, normals, backed = thicken_planar_visual(
                transformed, faces, uv, normals
            )
            if backed:
                self.disclosures.append(
                    f"{label}: planar original visual backed by 0.1 mm total thickness; original UVs and normals retained"
                )
        low, high = transformed.min(axis=0), transformed.max(axis=0)
        if np.any(high - low < 1e-7):
            raise ValueError(f"Planar source needs explicit conversion: {label}")
        center, extent = (low + high) / 2, high - low
        local = np.round(transformed - center, 7)
        local[np.abs(local) < 1e-7] = 0
        mesh = self.store(obj_bytes(local, faces, uv, normals), ".obj")
        obj = dict(
            id=f"source_{len(self.objects)}",
            label=label,
            kind="mesh",
            mesh_path=mesh,
            position=center.tolist(),
            size=extent.tolist(),
            visible=not physical,
            collision=physical,
            parent_id=parent,
            source_notice="NOTICE.txt",
            source_metadata="provenance.json",
        )
        if material:
            color, texture, opacity = material
            obj.update(
                color=color,
                texture_path=texture,
                opacity=opacity,
                surface={
                    "metallic": 0,
                    "roughness": 0.7,
                    "double_sided": True,
                    "alpha_mode": "BLEND" if opacity < 1 else "OPAQUE",
                },
            )
        self.objects.append(obj)
        return obj["id"]

    def collisions(self, mesh, scale):
        key = (str(mesh), tuple(scale))
        if key in self.collision_cache:
            return self.collision_cache[key]
        parts = []
        model_name = mesh.relative_to(self.source / "models").parts[0]
        curtain = self.entry["id"] == "hospital" and model_name in {
            "aws_robomaker_hospital_curtain_closed_01",
            "aws_robomaker_hospital_curtain_half_open_01",
            "aws_robomaker_hospital_curtain_open_01",
        }
        for primitive, unit in primitives(mesh, self.disclosures, root=self.source):
            vertices = primitive.vertex * unit * scale
            if model_name in self.external_models or curtain:
                # Only the known hospital scans and curtain hardware use
                # envelopes. Preserve the single >1m tall cloth component in
                # each pinned curtain so its U-shaped/open passage survives.
                preserve = (lambda v, f: np.ptp(v, axis=0)[2] > 1.0) if curtain else None
                prepared = prepare_static_envelopes(
                    vertices, primitive.vertex_index, preserve_surfaces=preserve
                )
            else:
                prepared = prepare_static_collision(vertices, primitive.vertex_index)
            parts.extend(prepared.parts)
            self.disclosures.append(f"{mesh.relative_to(self.source)}: {prepared.disclosure}")
        if not parts:
            raise ValueError(f"Missing collision surface: {mesh}")
        self.collision_cache[key] = parts
        return parts

    def model(self, instance, transform, label):
        transform = transform @ pose(instance)
        include = instance.find("include")
        if include is not None:
            uri = include.findtext("uri")
            if uri in ("model://sun", "model://ground_plane"):
                return
            path = self.source / "models" / uri.removeprefix("model://") / "model.sdf"
            self.input(path)
            model = xml(path).find("model")
            return self.model(model, transform @ pose(include), label)
        if instance.findall("joint"):
            raise ValueError(f"Articulated source needs a dedicated importer: {label}")
        for nested in instance.findall("model"):
            self.model(nested, transform, label + " / " + nested.get("name", "part"))
        assembly_parent = None
        for link in instance.findall("link"):
            frame = transform @ pose(link)
            parent = assembly_parent
            spheres = []
            for collision in link.findall("collision"):
                sphere = collision.find("geometry/sphere")
                if sphere is not None:
                    radius = float(sphere.findtext("radius"))
                    if not math.isfinite(radius) or radius <= 0:
                        raise ValueError("Invalid source sphere radius")
                    position = (frame @ pose(collision))[:3, 3]
                    obj = dict(
                        id=f"source_{len(self.objects)}",
                        label=label,
                        kind="sphere",
                        position=position.tolist(),
                        size=[2 * radius] * 3,
                        collision=True,
                        visible=False,
                        parent_id=parent,
                        source_notice="NOTICE.txt",
                        source_metadata="provenance.json",
                    )
                    self.objects.append(obj)
                    spheres.append(obj)
                    parent = parent or obj["id"]
                    continue
                path, scale = self.geometry(collision)
                for vertices, faces in self.collisions(path, scale):
                    oid = self.add_mesh(
                        vertices,
                        faces,
                        frame @ pose(collision),
                        label + " · source collision",
                        physical=True,
                        parent=parent,
                    )
                    parent = parent or oid
            if parent is None:
                raise ValueError(f"Visual-only source has no physical parent: {label}")
            assembly_parent = parent
            for visual in link.findall("visual"):
                sphere = visual.find("geometry/sphere")
                if sphere is not None:
                    radius = float(sphere.findtext("radius"))
                    position = (frame @ pose(visual))[:3, 3]
                    match = next(
                        (
                            o
                            for o in spheres
                            if np.allclose(o["position"], position)
                            and np.allclose(o["size"], [2 * radius] * 3)
                        ),
                        None,
                    )
                    if match is None:
                        raise ValueError("Unmatched source sphere visual and collision geometry")
                    color = np.fromstring(
                        visual.findtext("material/diffuse", ".65 .65 .65 1"), sep=" "
                    )
                    if (
                        color.shape != (4,)
                        or not np.isfinite(color).all()
                        or np.any(color < 0)
                        or np.any(color > 1)
                    ):
                        raise ValueError("Invalid source sphere material")
                    match.update(visible=True, color=color[:3].tolist(), opacity=float(color[3]))
                    continue
                path, scale = self.geometry(visual)
                for primitive, unit in primitives(path, self.disclosures, root=self.source):
                    uv = (
                        primitive.texcoordset[0][primitive.texcoord_indexset[0]]
                        if primitive.texcoordset
                        else None
                    )
                    normals = (
                        primitive.normal[primitive.normal_index]
                        if primitive.normal is not None
                        else None
                    )
                    scaled = frame @ pose(visual) @ np.diag([*(scale * unit), 1.0])
                    self.add_mesh(
                        primitive.vertex,
                        primitive.vertex_index,
                        scaled,
                        label,
                        parent=parent,
                        uv=uv,
                        normals=normals,
                        material=self.texture(primitive, path),
                    )

    def finish(self):
        low = np.min(
            [np.array(o["position"]) - np.array(o["size"]) / 2 for o in self.objects], axis=0
        )
        high = np.max(
            [np.array(o["position"]) + np.array(o["size"]) / 2 for o in self.objects], axis=0
        )
        offset = (low + high) / 2
        offset[2] = 0
        for obj in self.objects:
            obj["position"] = (np.array(obj["position"]) - offset).tolist()
        bounds = np.ceil(high - low + 2)
        bounds[2] = max(3.0, high[2] + 1)
        blockers = [
            (
                np.array(o["position"]) - np.array(o["size"]) / 2,
                np.array(o["position"]) + np.array(o["size"]) / 2,
            )
            for o in self.objects
            if o["collision"]
            and o["position"][2] + o["size"][2] / 2 > 0.04
            and o["position"][2] - o["size"][2] / 2 < 1.8
        ]

        def free(point):
            return all(
                not np.all((point >= a[:2] - 0.85) & (point <= b[:2] + 0.85)) for a, b in blockers
            )

        candidates = [
            np.array([x, y])
            for x in np.arange(-bounds[0] / 2 + 2, bounds[0] / 2 - 2, 0.5)
            for y in np.arange(-bounds[1] / 2 + 2, bounds[1] / 2 - 2, 0.5)
        ]
        candidates.sort(key=lambda p: np.linalg.norm(p))
        route = None
        for start in candidates:
            for direction in [
                np.array([1.0, 0]),
                np.array([0, 1.0]),
                np.array([-1.0, 0]),
                np.array([0, -1.0]),
            ]:
                if all(free(start + direction * d) for d in np.linspace(0, 2, 9)):
                    route = start, start + direction * 2, math.atan2(direction[1], direction[0])
                    break
            if route:
                break
        if route is None:
            raise ValueError("No verified clear starter corridor; choose spawn manually")
        start, target, yaw = route
        for index, (dx, dy, sx, sy) in enumerate(
            [(0, -0.5, 1.0, 0.03), (0, 0.5, 1.0, 0.03), (-0.5, 0, 0.03, 1.0), (0.5, 0, 0.03, 1.0)]
        ):
            self.objects.append(
                dict(
                    id=f"arrival_mark_{index}",
                    label="Blue arrival zone",
                    kind="box",
                    position=[float(target[0] + dx), float(target[1] + dy), 0.001],
                    size=[sx, sy, 0.002],
                    color=[0.1, 0.5, 0.9],
                    collision=False,
                )
            )
        spec = WorldSpec.model_validate(
            dict(
                name=self.entry["name"],
                description=self.entry["description"] + " Original AWS scene layout.",
                task="Drive into the blue arrival zone two metres ahead and remain there for one second.",
                robot="tiago++",
                bounds=bounds.tolist(),
                robot_start=[*start.tolist(), yaw],
                objects=self.objects,
                regions=[
                    dict(
                        id="arrival",
                        label="Blue arrival zone",
                        position=[*target.tolist(), 0.5],
                        size=[1.0, 1.0, 1.0],
                    )
                ],
                rubric=[
                    dict(
                        id="arrive",
                        description="Reach the arrival zone",
                        predicate="robot_in_region",
                        target_id="arrival",
                        duration=1,
                        tolerance=0.005,
                    )
                ],
                assumptions=[
                    "Imported source scene, not model-generated. All source equipment is static; manipulation is not certified.",
                    "Original layout/visual surfaces retained; source centimetres converted to metres, vertex coordinates quantized to 0.1 micrometre. Textures limited to 1024px; Phong materials mapped to estimated rough dielectric appearance.",
                    "Verified solid convex source collision components are retained. Architectural and curtain cloth contact surfaces use convex prisms with 0.5 mm total thickness, preserving their openings. Planar visual surfaces receive 0.1 mm backing. Per-mesh adaptations are recorded in provenance.",
                    "Hospital scanned props and disconnected curtain hardware use conservative component envelopes that can fill concavities and spaces under furniture. These fixed navigation props are not manipulation-certified; their original visual meshes remain intact.",
                    "Gazebo plugins, ROS launch code, source lights and dynamic actors are not executed. Robot Worlds supplies robot, ground and lighting. Source ODE-specific contact parameters are replaced by the runtime default isotropic friction 0.7.",
                    "A robot spawn and a short navigation task are added; the arrival volume allows 5 mm for compliant ground contact. Creation checks do not prove task solvability.",
                ],
                source_prompt="Imported from " + self.entry["source_url"],
            )
        )
        (self.output / "spec.json").write_text(spec.model_dump_json(indent=2))
        record = dict(
            self.entry,
            search={
                "query": "open source complete robot simulation environments warehouse house hospital bookstore",
                "catalogs": ["AWS Robotics official GitHub scene collection"],
                "selected": "Original complete scene layout, source visuals, textures and collision geometry",
                "alternatives": [
                    {
                        "source": "NVIDIA SimReady",
                        "reason": "Reusable objects rather than four complete environments",
                    },
                    {
                        "source": "RoboCasa",
                        "reason": "Kitchen-focused collection; selected four varied indoor spaces for these presets",
                    },
                ],
            },
            source_files=self.inputs,
            external_sources=self.external_sources,
            acquisition=json.loads((self.source / "acquisition.json").read_text()),
            disclosures=sorted(set(self.disclosures)),
            instances=len(xml(self.source / self.entry["world"]).find("world").findall("model")),
            components=len(self.objects),
            bounds=spec.bounds,
            robot_start=spec.robot_start,
        )
        (self.output / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
        (self.output / "NOTICE.txt").write_text(
            (self.source / "LICENSE").read_text()
            + "\n\nSource: "
            + self.entry["source_url"]
            + "\n\n"
            + "\n".join(spec.assumptions)
            + (
                "\n\n" + (self.source / "FUEL-NOTICE.txt").read_text()
                if self.external_sources
                else ""
            )
        )
        files = {
            str(p.relative_to(self.output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.output.rglob("*")
            if p.is_file()
        }
        (self.output / "manifest.json").write_text(
            json.dumps({"id": self.entry["id"], "files": files}, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "environment": self.entry["id"],
                    "components": len(self.objects),
                    "files": len(files),
                    "bytes": sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file()),
                    "bounds": spec.bounds,
                    "minimum_z": float(low[2]),
                }
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--only")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    failures = []
    for entry in json.loads(CATALOG.read_text())["environments"]:
        if args.only and entry["id"] != args.only:
            continue
        print("Preparing " + entry["id"], flush=True)
        source = acquire(entry, args.cache)
        if entry["id"] == "hospital":
            acquire_hospital_fuel(source, args.cache)
        output = args.output / entry["id"]
        adapter = Adapter(entry, source, output, args.cache)
        (output / "manifest.json").unlink(missing_ok=True)
        world = xml(source / entry["world"]).find("world")
        adapter.input(source / "LICENSE")
        adapter.input(source / entry["world"])
        for index, model in enumerate(world.findall("model")):
            print(
                f"{entry['id']} {index + 1}/{len(world.findall('model'))} {model.get('name')}",
                flush=True,
            )
            try:
                adapter.model(model, np.eye(4), model.get("name", "Source object"))
            except Exception as error:
                failure = {
                    "environment": entry["id"],
                    "source_model": model.get("name"),
                    "error": str(error),
                }
                failures.append(failure)
                print(json.dumps({"conversion_failure": failure}), flush=True)
                traceback.print_exc()
        if not any(f["environment"] == entry["id"] for f in failures):
            try:
                adapter.finish()
            except Exception as error:
                failure = {"environment": entry["id"], "source_model": None, "error": str(error)}
                failures.append(failure)
                print(json.dumps({"conversion_failure": failure}), flush=True)
                traceback.print_exc()
    if failures:
        (args.output / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
        raise RuntimeError(
            f"{len(failures)} source conversion failures; no partial scene is release-ready"
        )


if __name__ == "__main__":
    main()
