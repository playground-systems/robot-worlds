"""Tiny analytical geometry checks; no native renderer or physics engine."""
import copy
import json
import math
import struct

import numpy as np
import pytest

from robot_worlds.gltf_geometry import GLBGeometry


def scene():
    chunks, views, accessors = [], [], []

    def add(values, dtype, kind, components, normalized=False, stride=None):
        data = np.array(values, dtype=dtype).tobytes()
        offset = sum(len(c) for c in chunks)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        if stride:
            views[-1]["byteStride"] = stride
        chunks.append(data + b"\0" * (-len(data) % 4))
        accessors.append({"bufferView": len(views)-1, "byteOffset": 0, "count": len(values),
                          "componentType": kind, "type": components, "normalized": normalized})
        return len(accessors)-1

    position = add([[0, 0, 0, 999], [1, 0, 0, 999], [1, 1, 0, 999], [0, 1, 0, 999]], "<f4", 5126, "VEC3", stride=16)
    normal = add([[0, 0, 127, 0]]*4, "i1", 5120, "VEC3", normalized=True, stride=4)
    uv = add([[0, 0], [65535, 0], [65535, 65535], [0, 65535]], "<u2", 5123, "VEC2", normalized=True)
    color = add([[255, 128, 0, 255]]*4, "u1", 5121, "VEC4", normalized=True)
    indices = add([0, 1, 2, 0, 2, 3], "<u2", 5123, "SCALAR")
    image = b"test embedded image"
    views.append({"buffer": 0, "byteOffset": sum(len(c) for c in chunks), "byteLength": len(image)})
    chunks.append(image + b"\0" * (-len(image) % 4))
    binary = b"".join(chunks)
    document = {
        "asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views, "accessors": accessors,
        "scenes": [{"nodes": [0]}], "scene": 0,
        "nodes": [{"translation": [10, 20, 30], "rotation": [0, 0, math.sqrt(.5), math.sqrt(.5)],
                   "scale": [2, 3, 4], "children": [1]},
                  {"mesh": 0, "translation": [1, 0, 0], "extras": {"mass": 7, "joints": []}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": position, "NORMAL": normal,
                     "TEXCOORD_0": uv, "COLOR_0": color}, "indices": indices, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": .8}}],
        "textures": [{"extensions": {"EXT_texture_webp": {"source": 0}}, "sampler": 0}],
        "images": [{"mimeType": "image/webp", "bufferView": len(views)-1}],
        "samplers": [{"wrapS": 10497, "wrapT": 10497}],
    }
    return document, binary


def write(path, document, binary):
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 28+len(text)+len(binary), len(text), 0x4e4f534a)
                     + text + struct.pack("<II", len(binary), 0x004e4942) + binary)
    return GLBGeometry(path)


def test_hierarchy_scale_rotation_units_and_z_up_are_composed(tmp_path):
    doc, data = scene()
    geometry = write(tmp_path / "source.glb", doc, data)
    part, = geometry.primitives()
    assert part.node_index == 1 and part.mesh_index == 0 and part.material_index == 0
    assert part.positions == pytest.approx(np.array([[10, -30, 22], [10, -30, 24], [7, -30, 24], [7, -30, 22]]))
    assert part.normals == pytest.approx(np.array([[0, -1, 0]]*4))
    assert part.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert part.attributes["COLOR_0"][0] == pytest.approx([1, 128/255, 0, 1])
    assert geometry.document == doc  # Source joint frames/material data stay available.
    mime, image, sampler = geometry.texture_image({"index": 0})
    assert mime == "image/webp" and image == b"test embedded image"
    assert sampler == {"wrapS": 10497, "wrapT": 10497}


def test_negative_scale_flips_winding_and_normals_stay_perpendicular(tmp_path):
    doc, data = scene()
    doc["nodes"][0]["scale"] = [-2, 3, 4]
    part, = write(tmp_path / "source.glb", doc, data).primitives()
    assert part.triangles.tolist() == [[0, 2, 1], [0, 3, 2]]
    a, b, c = part.positions[part.triangles[0]]
    face_normal = np.cross(b-a, c-a)
    assert np.dot(face_normal, part.normals[0]) > 0
    assert np.dot(b-a, part.normals[0]) == pytest.approx(0)


def test_quantized_positions_and_oblique_normals_use_correct_normalization(tmp_path):
    doc, data = scene()
    raw = bytearray(data)
    for index, point in enumerate([[-32768, 0, 0], [32767, 0, 0], [32767, 32767, 0], [0, 32767, 0]]):
        struct.pack_into("<3h", raw, index*16, *point)
    doc["accessors"][0].update(componentType=5122, normalized=True)
    normal_offset = doc["bufferViews"][1]["byteOffset"]
    for i in range(4):
        struct.pack_into("4b", raw, normal_offset + i*4, 90, 90, 0, 0)
    part, = write(tmp_path / "quantized.glb", doc, bytes(raw)).primitives()
    assert part.positions[0] == pytest.approx([10, -30, 20])
    assert part.positions[1] == pytest.approx([10, -30, 24])
    # Source normal (1,1,0) is perpendicular to tangent (1,-1,0).
    # Under nonuniform scaling, applying the position matrix to the normal fails.
    tangent = part.transform[:3, :3] @ np.array([1, -1, 0])
    assert np.dot(tangent, part.normals[0]) == pytest.approx(0)
    assert np.linalg.norm(part.normals[0]) == pytest.approx(1)


def test_column_major_matrix_matches_trs_and_multiple_instances_are_preserved(tmp_path):
    doc, data = scene()
    matrix = np.array([[0, -3, 0, 10], [2, 0, 0, 20], [0, 0, 4, 30], [0, 0, 0, 1.]])
    doc["nodes"][0] = {"matrix": matrix.flatten(order="F").tolist(), "children": [1, 2]}
    doc["nodes"].append({"mesh": 0, "translation": [2, 0, 0]})
    first, second = write(tmp_path / "source.glb", doc, data).primitives()
    assert first.positions[0] == pytest.approx([10, -30, 22])
    assert second.positions[0] == pytest.approx([10, -30, 24])


def test_texture_transform_applied_before_obj_vertical_flip(tmp_path):
    doc, data = scene()
    geometry = write(tmp_path / "source.glb", doc, data)
    part, = geometry.primitives()
    original = part.attributes["TEXCOORD_0"].copy()
    info = {"index": 0, "extensions": {"KHR_texture_transform": {
        "offset": [.25, .5], "scale": [2, 3], "rotation": math.pi/2}}}
    assert geometry.texture_coordinates(part, info)[1] == pytest.approx([.25, 2.5])
    assert geometry.texture_coordinates(part, info, obj=True)[1] == pytest.approx([.25, -1.5])
    assert part.attributes["TEXCOORD_0"] == pytest.approx(original)
    material = geometry.material(0)
    material["pbrMetallicRoughness"]["metallicFactor"] = 0
    assert geometry.material(0)["pbrMetallicRoughness"]["metallicFactor"] == .8


@pytest.mark.parametrize("failure", ["cycle", "duplicate_parent", "missing_node", "matrix_and_trs", "singular", "quaternion",
    "sparse", "accessor_range", "stride", "unknown_type", "compressed", "negative_index", "float_index",
    "external_buffer", "external_image", "animation", "missing_scene", "nontriangles", "morph", "budget",
    "instancing", "required_extension"])
def test_invalid_or_unsupported_geometry_never_silently_flattens(tmp_path, failure):
    doc, data = scene()
    if failure == "cycle":
        doc["nodes"][1]["children"] = [0]
    elif failure == "duplicate_parent":
        doc["scenes"][0]["nodes"].append(1)
    elif failure == "missing_node":
        doc["nodes"][0]["children"] = [99]
    elif failure == "matrix_and_trs":
        doc["nodes"][0]["matrix"] = np.eye(4).flatten().tolist()
    elif failure == "singular":
        doc["nodes"][0]["scale"] = [0, 1, 1]
    elif failure == "quaternion":
        doc["nodes"][0]["rotation"] = [0, 0, 0, 0]
    elif failure == "sparse":
        doc["accessors"][0]["sparse"] = {}
    elif failure == "accessor_range":
        doc["accessors"][0]["byteOffset"] = 10000
    elif failure == "stride":
        doc["bufferViews"][0]["byteStride"] = 2
    elif failure == "unknown_type":
        doc["accessors"][0]["componentType"] = 999
    elif failure == "compressed":
        doc["bufferViews"][0]["extensions"] = {"EXT_meshopt_compression": {"buffer": 0}}
    elif failure == "negative_index":
        doc["meshes"][0]["primitives"][0]["indices"] = -1
    elif failure == "float_index":
        doc["accessors"][4]["componentType"] = 5126
    elif failure == "external_buffer":
        doc["buffers"][0]["uri"] = "https://example.com/model.bin"
    elif failure == "external_image":
        doc["images"][0]["uri"] = "../../file"
    elif failure == "animation":
        doc["animations"] = [{"channels": []}]
    elif failure == "missing_scene":
        del doc["scenes"]
    elif failure == "nontriangles":
        doc["meshes"][0]["primitives"][0]["mode"] = 1
    elif failure == "morph":
        doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    elif failure == "instancing":
        doc["nodes"][1]["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": 0}}}
    elif failure == "required_extension":
        doc["extensionsRequired"] = ["unknown_extension"]
    else:
        doc["accessors"][0]["count"] = 1000001
    with pytest.raises(ValueError):
        write(tmp_path / "source.glb", doc, data).primitives()


def test_out_of_range_indices_and_nonfinite_vertices_are_rejected(tmp_path):
    doc, data = scene()
    for target, payload in [(4, struct.pack("<H", 9)), (0, struct.pack("<f", float("nan")))]:
        damaged = bytearray(data)
        offset = doc["bufferViews"][target]["byteOffset"]
        damaged[offset:offset+len(payload)] = payload
        with pytest.raises(ValueError):
            write(tmp_path / "source.glb", doc, bytes(damaged)).primitives()


def test_unindexed_triangles_and_missing_uv_fail_explicitly(tmp_path):
    doc, data = scene()
    primitive = doc["meshes"][0]["primitives"][0]
    del primitive["indices"]
    for accessor in doc["accessors"][:4]:
        accessor["count"] = 3
    geometry = write(tmp_path / "source.glb", doc, data)
    part, = geometry.primitives()
    assert part.triangles.tolist() == [[0, 1, 2]]
    with pytest.raises(ValueError, match="missing UV"):
        geometry.texture_coordinates(part, {"index": 0, "texCoord": 1})


def test_selected_scene_is_required_when_ambiguous(tmp_path):
    doc, data = scene()
    doc["scenes"].append(copy.deepcopy(doc["scenes"][0]))
    del doc["scene"]
    with pytest.raises(ValueError, match="selected scene"):
        write(tmp_path / "source.glb", doc, data).primitives()
