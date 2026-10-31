"""Deterministic native display estimates; never change collision or dynamics."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

NOTICE = "Surface finishes, floor pattern and studio lighting are visual estimates, not measured materials."
PBR_RENDERER_NOTICE = "Source PBR maps are retained for the browser and MJCF export. The current classic MuJoCo policy/replay renderer uses base color and approximate Phong highlights; it does not reproduce normal/ORM maps, alpha masking, full colored emission or all source sampler settings. Material maps do not change contact physics."


def add_source_material(root, obj, asset_root, textures, materials):
    """Retain source PBR layers in MJCF, with explicit classic-renderer fallback."""
    from .mesh_assets import validate_texture
    surface = obj.surface
    if surface is None:
        raise ValueError("Source material requires explicit surface properties")
    roles = (("texture_path", "rgba", 4), ("normal_texture_path", "normal", 3),
             ("orm_texture_path", "orm", 3), ("emissive_texture_path", "emissive", 4))
    paths = []
    for field, role, channels in roles:
        value = getattr(obj, field)
        if value:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = Path(asset_root) / path
            paths.append((path.resolve(), role, channels))
    key = (surface.model_dump_json(), tuple(paths))
    if key in materials:
        return materials[key]
    asset = root.find("asset")
    if asset is None:
        asset = ET.SubElement(root, "asset")
    layers = []
    for path, role, channels in paths:
        texture_key = (path, channels)
        if texture_key not in textures:
            validate_texture(path)
            texture_name = f"source_texture_{len(textures)}"
            ET.SubElement(asset, "texture", name=texture_name, type="2d", file=str(path), nchannel=str(channels))
            textures[texture_key] = texture_name
        layers.append((role, textures[texture_key]))
    name = f"source_material_{len(materials)}"
    material = ET.SubElement(asset, "material", name=name, texuniform="false",
        metallic=str(surface.metallic), roughness=str(surface.roughness),
        specular=str(.04 + .76*surface.metallic), shininess=str((1-surface.roughness)**2),
        emission=str(max(surface.emissive)), rgba="1 1 1 1")
    for role, texture_name in layers:
        ET.SubElement(material, "layer", role=role, texture=texture_name)
    materials[key] = name
    return name


def finish_name(obj):
    text = re.sub(r"[_-]", " ", obj.id + " " + obj.label).lower()
    for name, pattern in (
        ("rubber", r"rubber|tire|tyre|gasket|conveyor belt"),
        ("cardboard", r"cardboard|carton|corrugated"),
        ("wood", r"wood|wooden|timber|plywood"),
        ("polymer", r"plastic|polymer|acrylic|polycarbonate|tote|bin"),
        ("architectural", r"wall|ceiling|concrete|masonry"),
        ("painted", r"painted|powder coated"),
        ("metal", r"steel|stainless|aluminum|aluminium|metal|brass|copper"),
        ("painted", r"rack|upright|rail|gantry"),
    ):
        if re.search(r"\b(" + pattern + r")\b", text):
            if name == "metal" and max(obj.color) > 0 and (max(obj.color)-min(obj.color))/max(obj.color) > .4:
                return "painted"
            return name
    return "matte"


def add_appearance(root):
    """Add a bounded palette and lights, preserving every physics attribute."""
    asset = root.find("asset")
    if asset is None:
        asset = ET.SubElement(root, "asset")
    visual = root.find("visual")
    if visual is None:
        visual = ET.SubElement(root, "visual")
    ET.SubElement(visual, "global", offwidth="960", offheight="720")
    ET.SubElement(visual, "quality", shadowsize="2048", offsamples="4")
    ET.SubElement(visual, "headlight", ambient=".28 .29 .31", diffuse=".28 .28 .28", specular=".15 .15 .15")
    ET.SubElement(asset, "texture", name="rw_sky", type="skybox", builtin="gradient",
                  rgb1=".20 .25 .32", rgb2=".68 .72 .76", width="256", height="1536")
    ET.SubElement(asset, "texture", name="rw_floor_texture", type="2d", builtin="checker",
                  rgb1=".58 .60 .61", rgb2=".55 .57 .58", mark="edge", markrgb=".48 .50 .51",
                  width="256", height="256")
    ET.SubElement(asset, "material", name="rw_floor", texture="rw_floor_texture", texuniform="true",
                  texrepeat=".5 .5", specular=".18", shininess=".3", reflectance="0", rgba="1 1 1 1")
    # Phong material controls are display estimates, independent of friction.
    profiles = {"matte": (.12, .12), "architectural": (.03, .05), "metal": (.8, .65),
                "painted": (.3, .4), "polymer": (.22, .3), "rubber": (.03, .06),
                "cardboard": (.02, .03), "wood": (.09, .12)}
    for name, (specular, shininess) in profiles.items():
        ET.SubElement(asset, "material", name="rw_" + name, specular=str(specular),
                      shininess=str(shininess), reflectance="0", rgba="1 1 1 1")
    world = root.find("worldbody")
    for name, direction, diffuse, shadow in (
        ("key", "-.4 .3 -1", ".75 .72 .65", "true"),
        ("fill", ".5 -.2 -1", ".28 .33 .42", "false"),
        ("rim", "0 -.6 -1", ".20 .22 .25", "false"),
    ):
        ET.SubElement(world, "light", name="rw_" + name, directional="true", pos="0 0 5",
                      dir=direction, diffuse=diffuse, specular=".3 .3 .3", castshadow=shadow)
