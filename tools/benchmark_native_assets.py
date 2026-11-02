"""Hosted comparison of shared versus per-instance original appliance assets.

No model calls or robot policy. Native compilation only; run on CI, not a
resource-constrained laptop. The baseline duplicates file assets as the earlier
compiler did while keeping every body's authored physics unchanged.
"""
import json
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_runtime_asset_reuse import per_geom_assets, scene_xml  # noqa: E402

from robot_worlds import load_prepared_asset  # noqa: E402
from robot_worlds.models import WorldSpec  # noqa: E402


def main():
    objects = []
    for i in range(24):
        asset = load_prepared_asset("nvidia-microwave079", prefix=f"appliance{i}",
                                    position=((i % 6 - 2.5)*.8, (i // 6 - 1.5)*.8, 0)).asset
        objects.extend(asset.objects)
    spec = WorldSpec(name="Repeated-source benchmark", description="Native compiler diagnostic only",
                     task="Inspect the appliance array", bounds=(8, 6, 3), objects=objects,
                     rubric=[dict(id="door", description="First door open", predicate="joint_at",
                                  object_id="appliance0_part9", threshold=-1)])
    shared = scene_xml(spec, Path("."))
    baseline = per_geom_assets(shared)
    results, physical = {}, {}
    for name, root in (("per_instance", baseline), ("shared", shared)):
        xml = ET.tostring(root, encoding="unicode")
        times = []
        for _ in range(3):
            start = time.perf_counter()
            model = mujoco.MjModel.from_xml_string(xml)
            times.append(time.perf_counter() - start)
            physical[name] = {key: getattr(model, key).copy() for key in
                              ("body_mass", "body_inertia", "body_pos", "geom_pos", "geom_quat", "jnt_pos")}
            results[name] = {"meshes": model.nmesh, "textures": model.ntex,
                             "texture_rgb_bytes": model.tex_data.nbytes,
                             "mesh_vertices": model.nmeshvert, "geoms": model.ngeom}
            del model
        results[name]["median_compile_seconds"] = statistics.median(times)
    for key in physical["shared"]:
        np.testing.assert_allclose(physical["shared"][key], physical["per_instance"][key], atol=1e-10)
    print(json.dumps({"instances": 24, "source_components": len(objects), "results": results,
                      "physics_arrays_equal": True, "policy_executed": False}, indent=2))


if __name__ == "__main__":
    main()
