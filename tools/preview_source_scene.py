"""Render an authored source-scene inspection without stepping physics.

Run as a bounded child process. This is source inspection, not robot integration
or task acceptance. Source XML/assets are never changed; cameras and the render
budget are applied only to the derived in-memory model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def preview(decisions_path: Path, output: Path):
    import mujoco
    import numpy as np
    from PIL import Image

    decisions = json.loads(decisions_path.read_text())
    if decisions["author"]["model"] != "gpt-6-astra" or decisions["author"]["reasoning"] != "high":
        raise ValueError("This preview requires the recorded Astra-high decisions")
    source = Path(decisions["source_xml"]).resolve()
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != decisions["source_sha256"]:
        raise ValueError("Source XML differs from the authored preview input")
    settings = decisions["render_settings"]
    width, height = settings["width"], settings["height"]
    if not (0 < width <= 640 and 0 < height <= 360 and settings["physics_steps"] == 0):
        raise ValueError("Preview exceeds its initial one-frame inspection budget")
    if output.exists():
        raise ValueError("Choose a new output directory; preserve previous evidence")
    output.mkdir(parents=True)
    root = ET.fromstring(original)
    if root.findall(".//include") or root.findall(".//plugin"):
        raise ValueError("Resolve dependencies and plugin policy before native inspection")
    from robot_worlds.source_scene_render import share_identical_textures
    texture_aliases = share_identical_textures(root)
    visual = root.find("visual")
    if visual is None:
        visual = ET.SubElement(root, "visual")
    for tag, values in [
        ("global", {"offwidth": width, "offheight": height}),
        ("quality", {"shadowsize": settings["max_shadowmap_size"], "offsamples": settings["multisamples"]}),
    ]:
        node = visual.find(tag)
        if node is None:
            node = ET.SubElement(visual, tag)
        for key, value in values.items():
            node.set(key, str(value))
    for view in decisions["views"]:
        eye, target, up = (np.asarray(view[key], dtype=float) for key in ("eye_m", "target_m", "up"))
        forward = (target - eye) / np.linalg.norm(target - eye)
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        ET.SubElement(root.find("worldbody"), "camera", name="rw_preview_" + view["id"],
                      pos=" ".join(map(str, eye)), xyaxes=" ".join(map(str, [*right, *up])),
                      fovy=str(view["vertical_fov_degrees"]))
    started = time.monotonic()
    record = {"author_decisions": str(decisions_path), "source_xml_sha256": decisions["source_sha256"],
              "mujoco_version": mujoco.__version__, "physics_steps": 0, "policy_runs": 0,
              "source_physics_modified": False, "scope": "Source visual inspection, not robot/task acceptance"}
    record["identical_texture_aliases"] = texture_aliases
    try:
        print("Compiling original source physics with authored preview cameras", flush=True)
        model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        record.update(native_compiled=True, bodies=model.nbody, joints=model.njnt, geoms=model.ngeom,
                      meshes=model.nmesh, mesh_vertices=len(model.mesh_vert), mesh_faces=len(model.mesh_face),
                      textures=model.ntex, materials=model.nmat, finite_initial_state=bool(np.isfinite(data.qpos).all()))
        print(json.dumps(record), flush=True)
        options = mujoco.MjvOption()
        options.geomgroup[:] = 0
        options.geomgroup[1] = 1
        record["visible_geom_groups"] = [1]
        record["visual_group_basis"] = "Original RoboCasa render_collision_mesh=False/render_visual_mesh=True; Astra author excludes group2 end-effector targets."
        images = []
        with mujoco.Renderer(model, height=height, width=width) as renderer:
            for view in decisions["views"]:
                renderer.update_scene(data, camera="rw_preview_" + view["id"], scene_option=options)
                filename = view["id"] + ".png"
                Image.fromarray(renderer.render()).save(output / filename)
                images.append(filename)
        mujoco.mj_saveModel(model, str(output / "preview-model.mjb"), None)
        record.update(images=images, compiled_preview_cache="preview-model.mjb", status="source_preview_complete")
        inventory = {
            "body_names": [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) for i in range(model.nbody)],
            "geom_types": {kind.name: int((model.geom_type == kind.value).sum()) for kind in mujoco.mjtGeom.__members__.values() if kind.value < 100},
            "visual_geom_types": {kind.name: int(((model.geom_type == kind.value) & (model.geom_group == 1)).sum()) for kind in mujoco.mjtGeom.__members__.values() if kind.value < 100},
            "materials": [{"id": i, "name": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MATERIAL, i),
                           "rgba": model.mat_rgba[i].tolist(), "texture_ids": model.mat_texid[i].tolist(),
                           "repeat": model.mat_texrepeat[i].tolist(), "uniform": bool(model.mat_texuniform[i]),
                           "metallic": float(model.mat_metallic[i]), "roughness": float(model.mat_roughness[i]),
                           "specular": float(model.mat_specular[i]), "shininess": float(model.mat_shininess[i])}
                          for i in range(model.nmat)],
            "texture_types": model.tex_type.tolist(), "texture_widths": model.tex_width.tolist(),
            "texture_heights": model.tex_height.tolist(), "texture_channels": model.tex_nchannel.tolist(),
        }
        (output / "native-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record.update(elapsed_seconds=time.monotonic() - started,
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                      * (1 if sys.platform == "darwin" else 1024),
                      source_xml_unchanged=source.read_bytes() == original)
        (output / "evidence.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    preview(args.decisions, args.output)
