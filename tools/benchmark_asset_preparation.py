"""Hosted diagnostic: compare OBJ preparation with the previous native probes.

Run from a development checkout. No model requests or policy episodes. The
baseline compiles one native model per mesh; use CI on resource-limited laptops.
"""
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_sourced_household import sourced_scene  # noqa: E402

from robot_worlds import mesh_assets  # noqa: E402
from robot_worlds.runtime import Runtime  # noqa: E402


def main():
    start = time.perf_counter()
    spec, _, _ = sourced_scene()
    cold = time.perf_counter() - start
    start = time.perf_counter()
    sourced_scene()
    warm = time.perf_counter() - start
    runtime = object.__new__(Runtime)
    runtime.spec, runtime.asset_root = spec, Path(".")
    runtime._objects = {o.id: o for o in spec.objects}
    probes = []
    direct = mesh_assets.obj_bounds

    def legacy(path):
        root = ET.fromstring("<mujoco><asset/><worldbody/></mujoco>")
        ET.SubElement(root.find("asset"), "mesh", name="probe", file=str(path))
        ET.SubElement(root.find("worldbody"), "geom", type="mesh", mesh="probe")
        model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, model.geom_quat[0])
        vertices = model.mesh_vert @ rotation.reshape(3, 3).T + model.geom_pos[0]
        bounds = np.array([vertices.min(0), vertices.max(0)])
        probes.append((path, bounds))
        return bounds

    def prepare():
        root = ET.fromstring("<mujoco><asset/><worldbody/></mujoco>")
        start = time.perf_counter()
        runtime._append_objects(root)
        return time.perf_counter() - start

    # Measure baseline without timing the additional bounds-equivalence check.
    with patch.object(mesh_assets, "obj_bounds", legacy):
        legacy_seconds = prepare()
    fast_seconds = prepare()
    errors = [float(np.max(np.abs(bounds - np.array(direct(path))))) for path, bounds in probes]
    assert max(errors) < 1e-6, errors
    print(json.dumps({"scene_components": len(spec.objects), "cold_import_ms": cold*1000,
                      "warm_import_ms": warm*1000, "removed_native_probes": len(probes),
                      "legacy_preparation_ms": legacy_seconds*1000,
                      "direct_preparation_ms": fast_seconds*1000,
                      "max_bounds_difference_m": max(errors)}, indent=2))


if __name__ == "__main__":
    main()
