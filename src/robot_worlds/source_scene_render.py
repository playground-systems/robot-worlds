"""Derived rendering optimizations that leave source scene files untouched."""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET


def share_identical_textures(root: ET.Element) -> dict[str, str]:
    """Coalesce exactly equal file textures in a derived in-memory MJCF tree.

    Texture names are retained as aliases in the returned map. Every attribute,
    child and file path must match; no resolution, pixels or material value is
    changed. This is for frozen previews, not models that mutate named textures
    independently at runtime. Procedural textures retain separate identities.
    """
    textures = root.findall("./asset/texture")
    names = [node.get("name") for node in textures if node.get("name")]
    if len(set(names)) != len(names):
        raise ValueError("Texture names must be unique before optimization")
    aliases, canonical = {}, {}
    for asset in root.findall("asset"):
        for node in list(asset.findall("texture")):
            if not node.get("name") or not any(key == "file" or key.startswith("file") for key in node.attrib):
                continue
            equivalent = copy.deepcopy(node)
            equivalent.attrib.pop("name")
            equivalent.attrib = dict(sorted(equivalent.attrib.items()))
            equivalent.tail = None
            identity = ET.tostring(equivalent, encoding="utf-8")
            name = node.get("name")
            if identity in canonical:
                aliases[name] = canonical[identity]
                asset.remove(node)
            else:
                canonical[identity] = name
    for node in root.iter():
        if node.tag in {"material", "layer"} and node.get("texture") in aliases:
            node.set("texture", aliases[node.get("texture")])
    return aliases
