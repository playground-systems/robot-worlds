"""Original-asset fixture shared by pure SDK tests and hosted native checks."""
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from robot_worlds import World, asset_import, import_convex_mesh_asset, import_mjcf_asset, load
from robot_worlds.generator import validate_spec
from robot_worlds.mesh_assets import read_triangle_obj
from robot_worlds.models import SceneObject, WorldSpec

SOURCES = Path(asset_import.__file__).parent / "assets/household"


def sourced_scene(*, inside=False):
    microwave = import_mjcf_asset(SOURCES / "microwave079/model.xml", prefix="microwave", position=(2, 0, .8))
    root = SOURCES / "porcelain_bowl"
    bowl = import_convex_mesh_asset(root / "meshes/model.obj", root / "collision/manifest.json",
                                    prefix="bowl", mass=.6, source_notice=root / "ATTRIBUTION.md",
                                    texture_path=root / "materials/textures/texture.png")
    objects = {o.id: o for o in microwave.objects}
    tray = objects[microwave.geom_ids["tray"]]
    height = tray.position[2]+tray.size[2]/2 if inside else .6
    minimum = min(o.position[2]-o.size[2]/2 for o in bowl.objects if o.collision)
    translation = np.array([tray.position[0] if inside else 1, tray.position[1] if inside else 0, height-minimum+.001])
    for obj in bowl.objects:
        obj.position = np.array(obj.position)+translation
    subject = bowl.body_ids["object"]
    spec = WorldSpec(
        name="Original bowl and microwave", description="Google scanned bowl and NVIDIA/Lightwheel articulated appliance",
        task="Put the white porcelain bowl inside the microwave and leave it resting there.", bounds=(8, 6, 3),
        objects=[SceneObject(id="source_counter", label="Bowl counter", kind="box", position=(1, 0, .3), size=(.6, .5, .6)),
                 SceneObject(id="appliance_counter", label="Microwave counter", kind="box", position=(2, 0, .32715), size=(.85, .7, .6543)),
                 *microwave.objects, *bowl.objects],
        regions=microwave.regions,
        assumptions=[*microwave.disclosures, *bowl.disclosures, "Bowl mass is estimated as 0.6 kg; it is not a measured source property."],
        rubric=[dict(id="inside", description="Entire bowl inside cavity", predicate="in_region", object_id=subject,
                     target_id=microwave.regions[0].id, tolerance=.002, duration=.2, intent="maintain" if inside else "achieve"),
                dict(id="supported", description="Bowl contacts tray", predicate="on_top", object_id=subject,
                     target_id=tray.id, tolerance=.005, duration=.2, intent="maintain" if inside else "achieve"),
                dict(id="released", description="No gripper holding", predicate="released", object_id=subject,
                     intent="maintain", duration=.2)],
    )
    if inside:
        spec.task = "Keep the bowl resting inside the microwave. This is a pre-placed stability fixture."
    return spec, bowl, microwave


def test_pinned_bowl_surface_is_unchanged_and_conversion_keeps_cavity_empty():
    root = SOURCES / "porcelain_bowl"
    for item in json.loads((root / "source-manifest.json").read_text())["files"]:
        assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    manifest = json.loads((root / "collision/manifest.json").read_text())
    assert len(manifest["parts"]) == 158
    # A region comfortably above the scanned inner floor must remain empty.
    samples = np.array([(x, y, z) for x in (-.025, 0, .025) for y in (-.025, 0, .025) for z in (.035, .05)])
    for part in manifest["parts"]:
        vertices, faces = read_triangle_obj(root / "collision" / part["file"])
        points = vertices[faces]
        normals = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
        direction = np.einsum("ij,ij->i", normals, vertices.mean(0)-points[:, 0])
        normals[direction > 0] *= -1
        signed = samples @ normals.T - np.einsum("ij,ij->i", normals, points[:, 0])
        assert not np.any(np.all(signed <= 1e-12, axis=1)), part["file"]


def test_original_household_scene_has_no_static_errors_and_retains_mass():
    spec, bowl, _ = sourced_scene()
    assert validate_spec(spec) == []
    assert sum(o.mass for o in bowl.objects if o.collision) == pytest.approx(.6)
    assert sum(o.movable for o in bowl.objects) == 1


def test_frozen_world_exports_original_textures_notices_and_conversion_settings(tmp_path):
    spec, _, _ = sourced_scene()
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    world = load(world.path)
    sources = {o.source_metadata for o in world.spec.objects if o.source_metadata}
    assert len(sources) == 2
    conversion = next(json.loads((world.path / path).read_text()) for path in sources if "parts" in json.loads((world.path / path).read_text()))
    assert conversion["settings"]["threshold"] == .002
    assert conversion["tool_version"] == "1.0.14"
    expected = {path for o in world.spec.objects for path in (o.texture_path, o.source_notice, o.source_metadata) if path}
    with zipfile.ZipFile(world.export(tmp_path / "original-assets.zip")) as archive:
        assert expected <= set(archive.namelist())
        archive.extractall(tmp_path / "imported")
    assert load(tmp_path / "imported").id == world.id
