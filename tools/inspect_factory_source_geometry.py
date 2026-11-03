#!/usr/bin/env python3
"""Measure selected original meshes with existing codecs; do not author physics.

The supervisor runs one isolated asset worker at a time and watches worker/child
RSS. Reports contain original topology/material identity and rest-pose bounds,
not cavity approval, collision geometry, placement or simulated performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from prepare_factory_v3_sources import atomic_bytes, digest, encoded, read_glb, safe_path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO / ".robot-worlds/factory-v3-sources-2026-11-02"
FIRST = ("equipment_rolling_tool_chest_cabinet_combo_d01", "equipment_utility_cart_e01",
         "storage_toolbox_standard_hardCase_d01", "crate_blue_a01", "crate_white_a20",
         "dishware_plastic_tray_a01", "equipment_bench_vise_a01")
PHYSICS = "KHR_physics_rigid_bodies"


def peak_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def source_record(root, asset):
    manifest_path = safe_path(root, "source-manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest["status"] != "all_selected_source_bytes_resolved_no_import_or_physics_validation":
        raise ValueError("Complete source acquisition is required")
    selection_path = safe_path(root, "source-selection.json")
    if digest(selection_path) != manifest["selection_sha256"]:
        raise ValueError("Author source selection changed")
    matches = [row for row in manifest["records"] if row["asset_id"] == asset]
    if len(matches) != 1:
        raise ValueError("Asset is not in the acquired author selection")
    record = matches[0]
    for file in record["files"]:
        if "path" not in file:
            continue
        path = safe_path(root, file["path"])
        if path.stat().st_size != file["bytes"] or digest(path) != file["sha256"]:
            raise ValueError("Original source dependency hash/length changed")
    return record


def inspect_asset(root, output, asset):
    # Imports stay in the watched worker, never in the lightweight supervisor.
    sys.path.insert(0, str(REPO / "src"))
    import numpy as np
    from PIL import Image

    from robot_worlds.gltf_decode import decode_glb
    from robot_worlds.gltf_geometry import GLBGeometry, _transform

    record = source_record(root, asset)
    source = safe_path(root, record["files"][0]["path"])
    source_json = read_glb(source)
    decoded = decode_glb(source, output / "decoded", timeout=40)
    geometry = GLBGeometry(decoded.path)
    doc = geometry.document
    preserved = {}
    for field in ("nodes", "meshes", "materials", "textures", "images", "samplers", "scenes", "scene"):
        preserved[field] = source_json.get(field) == doc.get(field)
    preserved["physics_extension"] = source_json.get("extensions", {}).get(PHYSICS) == doc.get("extensions", {}).get(PHYSICS)
    if not all(preserved.values()):
        raise ValueError("Decoder changed source hierarchy/material/physics metadata")
    primitives = geometry.primitives()
    nodes = doc["nodes"]
    parents = {child: index for index, node in enumerate(nodes) for child in node.get("children", [])}
    z_up = np.array([[1., 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
    matrices = {}

    def world(index):
        if index not in matrices:
            matrices[index] = (world(parents[index]) if index in parents else np.eye(4)) @ _transform(nodes[index])
        return matrices[index]

    def bounds(values):
        points = np.concatenate(values)
        low, high = points.min(axis=0), points.max(axis=0)
        return {"min": low.tolist(), "max": high.tolist(), "extent": (high - low).tolist()}

    def y_up(points):
        return points @ z_up[:3, :3]

    body_indices = {index for index, node in enumerate(nodes)
                    if "motion" in node.get("extensions", {}).get(PHYSICS, {})}

    def owner(index):
        while index not in body_indices and index in parents:
            index = parents[index]
        return index if index in body_indices else None

    parts, bodies = [], []
    for part in primitives:
        parts.append({"mesh_node_index": part.node_index, "mesh_node_name": nodes[part.node_index].get("name"),
            "mesh_index": part.mesh_index, "primitive_index": part.primitive_index,
            "body_node_index": owner(part.node_index), "material_index": part.material_index,
            "vertices": len(part.positions), "triangles": len(part.triangles),
            "bounds_model_glb_y_up_m": bounds([y_up(part.positions)]),
            "bounds_inspection_z_up_m": bounds([part.positions]),
            "transform_local_accessor_to_inspection": part.transform.tolist()})
    for index in sorted(body_indices):
        owned = [part for part in primitives if owner(part.node_index) == index]
        body = {"node_index": index, "name": nodes[index].get("name"),
                "motion_original": nodes[index]["extensions"][PHYSICS]["motion"],
                "extras_original": nodes[index].get("extras", {}),
                "matrix_body_local_to_model_glb_y_up": world(index).tolist(),
                "matrix_body_local_to_inspection_z_up": (z_up @ world(index)).tolist(),
                "mesh_node_indices": sorted({p.node_index for p in owned})}
        if owned:
            body["bounds_model_glb_y_up_m"] = bounds([y_up(p.positions) for p in owned])
            body["bounds_inspection_z_up_m"] = bounds([p.positions for p in owned])
            matrix = z_up @ world(index)
            linear = matrix[:3, :3]
            rigid = bool(np.allclose(linear.T @ linear, np.eye(3), rtol=0, atol=1e-5))
            body["body_frame_is_metric_rigid"] = rigid
            if rigid:
                inverse = np.linalg.inv(matrix)
                body["bounds_body_local_m"] = bounds([
                    p.positions @ inverse[:3, :3].T + inverse[:3, 3] for p in owned])
        bodies.append(body)
    joints = []
    for index, node in enumerate(nodes):
        for joint in node.get("extras", {}).get("joints", []):
            measured = {"child_body_node_index": index, "source_model_y_up": joint}
            if "pivot" in joint:
                measured["pivot_inspection_z_up_m"] = (z_up[:3, :3] @ np.asarray(joint["pivot"])).tolist()
            if "axis" in joint:
                measured["axis_inspection_z_up"] = (z_up[:3, :3] @ np.asarray(joint["axis"])).tolist()
            joints.append(measured)
    images = []
    for index in range(len(doc.get("images", []))):
        mime, data = geometry.image(index)
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
        path = safe_path(output, f"images/{asset}/image-{index:03d}.{extension}")
        atomic_bytes(path, data)
        with Image.open(path) as image:
            dimensions = [image.width, image.height]
        images.append({"index": index, "path": str(path.relative_to(output)), "mime_type": mime,
                       "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "dimensions_px": dimensions, "transformation": "none; embedded original bytes"})
    report = {"schema_version": 1, "asset_id": asset,
        "status": "decoded_source_geometry_inspected_not_simulation_ready",
        "source_sha256": record["files"][0]["sha256"], "source_glb": str(source.relative_to(root)),
        "source_files_unchanged": digest(source) == record["files"][0]["sha256"],
        "decoded_glb": str(decoded.path.relative_to(output)),
        "decode_manifest": json.loads(decoded.manifest_path.read_text()),
        "inspector_sha256": digest(Path(__file__)),
        "geometry_reader_sha256": digest(REPO / "src/robot_worlds/gltf_geometry.py"),
        "original_metadata_preserved": preserved,
        "coordinate_contract": {
            "source": "original glTF model rest pose, metres, Y-up; no rescaling or placement",
            "inspection": "metres, Z-up using existing geometry reader: [x, -z, y]",
            "joint_extras": "publisher declares pivot/axis in source model space at rest; limits/units retained",
            "matrix_model_y_up_to_inspection_z_up": z_up.tolist()},
        "publisher_manifest_bounds_model_glb_y_up_m": record["optimized_manifest_record"]["bounds"],
        "bounds_model_glb_y_up_m": bounds([y_up(p.positions) for p in primitives]),
        "bounds_inspection_z_up_m": bounds([p.positions for p in primitives]),
        "counts": {"nodes": len(nodes), "mesh_primitives": len(primitives), "motion_bodies": len(bodies),
                   "joint_definitions": len(joints), "vertices": sum(len(p.positions) for p in primitives),
                   "triangles": sum(len(p.triangles) for p in primitives),
                   "materials": len(doc.get("materials", [])), "original_images": len(images)},
        "source_hierarchy": [{"index": i, "parent_index": parents.get(i), "source_node": n}
                             for i, n in enumerate(nodes)],
        "parts": parts, "bodies": bodies, "joints": joints,
        "materials_original": doc.get("materials", []), "textures_original": doc.get("textures", []),
        "samplers_original": doc.get("samplers", []), "images": images,
        "physics_extension_original": doc.get("extensions", {}).get(PHYSICS, {}),
        "cavity_and_handle_status": "Only raw rest-pose vertex bounds were measured. Interior/handle clearances, "
            "connectivity, swept clearances and contact behavior are not established. No colliders were created.",
        "peak_worker_rss_bytes": peak_bytes()}
    report["measured_minus_manifest_bounds_m"] = {
        field: (np.asarray(report["bounds_model_glb_y_up_m"][field])
                - np.asarray(record["optimized_manifest_record"]["bounds"][field])).tolist()
        for field in ("min", "max")}
    if not report["source_files_unchanged"]:
        raise ValueError("Source GLB changed during inspection")
    path = safe_path(output, f"reports/{asset}.json")
    atomic_bytes(path, encoded(report), replace=True)
    print(json.dumps({"asset": asset, "counts": report["counts"],
                      "bounds_z_up_m": report["bounds_inspection_z_up_m"],
                      "peak_worker_rss_bytes": report["peak_worker_rss_bytes"]}), flush=True)


def process_snapshot():
    text = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True, timeout=3)
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 3:
            pid, parent, rss = map(int, fields)
            result[pid] = (parent, rss * 1024)
    return result


def watched_worker(root, output, asset, limit, timeout):
    log = safe_path(output, f"logs/{asset}.log")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP"}}
    env.update(PYTHONPATH=str(REPO / "src"), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    started, observed = time.monotonic(), {}
    with log.open("w") as stream:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            "--sources", str(root), "--output", str(output), "--worker", asset],
            cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            while process.poll() is None:
                snapshot = process_snapshot()
                descendants = {process.pid}
                changed = True
                while changed:
                    found = {pid for pid, (parent, _) in snapshot.items() if parent in descendants}
                    changed = bool(found - descendants)
                    descendants.update(found)
                for pid in descendants:
                    if pid in snapshot:
                        observed[pid] = max(observed.get(pid, 0), snapshot[pid][1])
                if any(value > limit for value in observed.values()):
                    raise RuntimeError("Geometry worker/decoder exceeded the per-process RSS watchdog")
                if time.monotonic() - started > timeout:
                    raise RuntimeError("Geometry worker exceeded its time watchdog")
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError(f"Geometry worker failed; inspect {log}")
            report_path = safe_path(output, f"reports/{asset}.json")
            report = json.loads(report_path.read_text())
            if report["peak_worker_rss_bytes"] > limit:
                raise RuntimeError("Worker self-reported a peak over the memory limit")
            return {"asset_id": asset, "report": str(report_path.relative_to(output)),
                    "report_sha256": digest(report_path), "elapsed_seconds": round(time.monotonic() - started, 3),
                    "memory_limit_bytes_per_process": limit, "watchdog_poll_seconds": .1,
                    "observed_peak_process_rss_bytes": max(observed.values(), default=0),
                    "self_reported_peak_worker_rss_bytes": report["peak_worker_rss_bytes"],
                    "status": "source_inspection_complete_not_native_validated"}
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--asset", action="append", help="Inspect only an already selected source ID")
    parser.add_argument("--memory-mb", type=int, default=400)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.sources.resolve()
    raw_output = args.output or root / "geometry-inspection"
    if args.sources.is_symlink() or raw_output.is_symlink():
        raise ValueError("Inspection roots cannot be symlinks")
    output = raw_output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        inspect_asset(root, output, args.worker)
        return
    if not 128 <= args.memory_mb <= 400 or not 1 <= args.timeout <= 120:
        raise ValueError("Per-process watchdog must be 128..400 MB, deadline 1..120 seconds")
    if not shutil.which("node"):
        raise RuntimeError("Existing Node.js decoder runtime is missing; no installation attempted")
    manifest = json.loads(safe_path(root, "source-manifest.json").read_text())
    selected = [r["asset_id"] for r in manifest["records"]]
    order = args.asset or [a for a in FIRST if a in selected] + [a for a in selected if a not in FIRST]
    if not order or len(set(order)) != len(order) or set(order) - set(selected):
        raise ValueError("Inspection accepts only distinct acquired author-selected IDs")
    results = []
    for asset in order:
        row = watched_worker(root, output, asset, args.memory_mb * 1_000_000, args.timeout)
        results.append(row)
        atomic_bytes(safe_path(output, "inspection-index.json"), encoded({
            "schema_version": 1, "status": "source_geometry_only_no_asset_readiness_claim",
            "source_manifest_sha256": digest(safe_path(root, "source-manifest.json")),
            "inspector_sha256": digest(Path(__file__)), "assets": results}), replace=True)
        print(f"Inspected {asset}; observed process peak {row['observed_peak_process_rss_bytes']:,} bytes", flush=True)


if __name__ == "__main__":
    main()
