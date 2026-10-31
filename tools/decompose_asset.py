"""Offline source-mesh conversion. Run on CI/a worker, not during scene rendering.

Requires coacd==1.0.14. Original visuals/UVs/textures are never rewritten.
The output is an approximate collision representation requiring native checks.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from robot_worlds.mesh_assets import convex_volume, read_triangle_obj

SETTINGS = dict(threshold=.002, real_metric=True, preprocess_mode="auto",
                preprocess_resolution=100, resolution=2000, mcts_nodes=20,
                mcts_iterations=100, mcts_max_depth=3, seed=0,
                merge=True, decimate=True, max_ch_vertex=128, max_convex_hull=-1)


def decompose(source: Path, output: Path):
    import coacd
    version = importlib.metadata.version("coacd")
    if version != "1.0.14":
        raise ValueError("Reproducible asset conversion requires coacd==1.0.14")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty conversion output directory")
    vertices, faces = read_triangle_obj(source)
    parts = coacd.run_coacd(coacd.Mesh(vertices, faces), **SETTINGS)
    if not 1 <= len(parts) <= 128:
        raise ValueError("Decomposition must have 1–128 pieces; review parameters rather than silently merging")
    # Check every hull before writing any outputs.
    volumes = [convex_volume(v, f) for v, f in parts]
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, ((verts, triangles), volume) in enumerate(zip(parts, volumes)):
        filename = f"part-{index:03}.obj"
        lines = ["# Derived collision mesh; original visual asset is unchanged."]
        lines += ["v " + " ".join(f"{float(x):.17g}" for x in row) for row in verts]
        lines += ["f " + " ".join(str(int(x)+1) for x in row) for row in triangles]
        data = ("\n".join(lines)+"\n").encode()
        (output / filename).write_bytes(data)
        records.append({"file": filename, "sha256": hashlib.sha256(data).hexdigest(),
                        "vertices": len(verts), "triangles": len(triangles), "volume_m3": volume})
    manifest = {"schema_version": 1, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "tool": "CoACD", "tool_version": version, "settings": SETTINGS, "parts": records,
                "limitations": ["Approximate convex collision decomposition, not the original visual mesh.",
                                "The 2 mm concavity setting is a converter parameter, not a certified maximum surface error.",
                                "Native cavity/contact checks are required before physical use."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"parts": len(parts), "volume_m3": sum(volumes), "output": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    decompose(args.source, args.output)
