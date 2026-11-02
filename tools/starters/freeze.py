"""Native-validate prepared source scenes and package offline starter bundles.

Run on a Linux release worker under Xvfb, after ``prepare.py``. No model calls,
upstream code, or customer-time conversion are involved. A failed native check
leaves diagnostics and never produces a release catalog claiming availability.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from robot_worlds.artifacts import file_digest, inside, public_error, write_json
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World, load

CATALOG = Path(__file__).resolve().parents[2] / "src/robot_worlds/assets/starters/catalog.json"
REQUIRED_CHECKS = {
    "schema", "geometry_and_rubric", "physics_settling", "physics_reset", "initial_rubric", "render",
}


def verify_prepared(root: Path, identifier: str) -> dict:
    """Reject incomplete/tampered conversion input before opening native assets."""
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("id") != identifier or not isinstance(manifest.get("files"), dict):
        raise ValueError("Prepared scene manifest does not identify this starter")
    expected = manifest["files"]
    actual = {str(path.relative_to(root)) for path in root.rglob("*")
              if path.is_file() and path != root / "manifest.json"}
    if not {"spec.json", "provenance.json", "NOTICE.txt"} <= expected.keys() or actual != expected.keys():
        raise ValueError("Prepared scene files do not match their manifest")
    for member, checksum in expected.items():
        path = inside(root, member)
        if path.is_symlink() or file_digest(path) != checksum:
            raise ValueError(f"Prepared scene checksum mismatch: {member}")
    return json.loads((root / "provenance.json").read_text())


def require_native_ready(manifest: dict) -> None:
    validation = manifest.get("validation", {})
    checks = validation.get("checks", [])
    by_name = {check.get("check"): check for check in checks}
    runtime = manifest.get("runtime", {})
    if (validation.get("status") != "ready" or validation.get("profile") != "fixed_reference"
            or runtime.get("engine") != "mujoco"
            or not REQUIRED_CHECKS <= by_name.keys()
            or any(check.get("passed") is not True for check in checks)
            or by_name["physics_settling"].get("status") != "passed"
            or not by_name["physics_settling"].get("evidence", {}).get("complete")):
        raise ValueError("Starter requires complete, successful native MuJoCo validation")
    for key in ("model_fingerprint", "implementation_fingerprint"):
        value = runtime.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"Missing native {key}")


def cutaway_object_ids(spec: WorldSpec) -> list[str]:
    """Only explicitly named, sourced roof/ceiling meshes receive a cutaway."""
    result = []
    fixtures = {"light", "lamp", "fan", "fixture", "vent", "camera", "sensor", "sprinkler", "sign"}
    for obj in spec.objects:
        tokens = set(re.split(r"[^a-z0-9]+", (obj.id + " " + obj.label).lower()))
        if (obj.kind == "mesh" and obj.visible and obj.source_notice and obj.source_metadata
                and tokens & {"ceiling", "roof"} and not tokens & fixtures):
            result.append(obj.id)
    return sorted(result)


def render_native_overview(runtime):
    """Render an intentional aerial inspection view without orbit collision repair.

    Only the thumbnail uses this cutaway. Source bodies, contact settings,
    simulator state and policy cameras are retained. The runtime background
    ground is hidden to expose the original source flooring. Every temporary
    display change is restored before returning, including on renderer failure.
    """
    import mujoco

    renderer = runtime._get_renderer(overview=True)
    renderer.disable_depth_rendering()
    renderer.disable_segmentation_rendering()
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    bounds = runtime.spec.bounds
    focus_z = min(1, bounds[2] / 3)
    camera.lookat[:] = [0, 0, focus_z]
    fovy = float(runtime.model.vis.global_.fovy)
    if not 0 < fovy < 179:
        raise ValueError("Native overview camera has an invalid field of view")
    # Fit the complete bounds inside the vertical field of view, including a
    # small margin. This avoids scene-size-dependent crops at a fixed distance.
    radius = math.sqrt((bounds[0] / 2) ** 2 + (bounds[1] / 2) ** 2
                       + max(focus_z, bounds[2] - focus_z) ** 2)
    camera.distance = max(4, radius / math.sin(math.radians(fovy) / 2) * 1.08)
    camera.azimuth, camera.elevation = 135, -65
    hidden = set(cutaway_object_ids(runtime.spec))
    geom_ids = [gid for gid, oid in runtime._geom_objects.items() if oid in hidden]
    # Both supported robot builders name their added ground geom "floor".
    # Source floors have their own geom_<object_id> names and remain visible.
    geom_ids.append(int(runtime.model.geom("floor").id))
    geom_ids = sorted(set(geom_ids))
    original = runtime.model.geom_rgba[geom_ids].copy()
    try:
        runtime.model.geom_rgba[geom_ids, 3] = 0
        renderer.update_scene(runtime.data, camera=camera)
        pixels = renderer.render().copy()
    finally:
        runtime.model.geom_rgba[geom_ids] = original
    image = io.BytesIO()
    Image.fromarray(pixels).save(image, format="PNG")
    return image.getvalue()


def overview_runtime(spec, **kwargs):
    """Use ordinary native reset checks with a disclosed aerial cutaway preview."""
    from robot_worlds.runtime import Runtime

    class OverviewRuntime(Runtime):
        def render(self):
            return render_native_overview(self)

    return OverviewRuntime(spec, **kwargs)


def freeze_one(entry: dict, source: Path, output: Path, evidence: Path) -> dict:
    identifier = entry["id"]
    provenance = verify_prepared(source, identifier)
    for key in ("id", "repo", "revision", "world", "license", "source_url"):
        if provenance.get(key) != entry.get(key):
            raise ValueError(f"Prepared provenance does not match pinned catalog: {key}")
    spec = WorldSpec.model_validate_json((source / "spec.json").read_text())
    hidden_ids = cutaway_object_ids(spec)
    thumbnail_view = (f"Native MuJoCo aerial cutaway at reset; {len(hidden_ids)} explicitly named source ceiling/roof visual objects hidden for the thumbnail only."
                      if hidden_ids else "Native MuJoCo aerial inspection at reset; all source visible geometry retained.")
    thumbnail_view += " Runtime background ground hidden to show the original source flooring."
    spec.assumptions.append(thumbnail_view + " All saved source geometry, collision physics and policy-camera visibility are unchanged.")
    evidence.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    # Stage outside the release output: partial validation never looks installed.
    with tempfile.TemporaryDirectory(prefix=f".{identifier}-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        world = World.from_spec(spec, temporary / "worlds", asset_root=source,
                                validation_profile="fixed_reference", runtime_factory=overview_runtime)
        write_json(evidence / f"{identifier}-native-validation.json", {
            "starter_id": identifier, "world_id": world.id,
            "prepared_manifest_sha256": file_digest(source / "manifest.json"),
            "validation": world.manifest["validation"], "runtime": world.manifest.get("runtime", {}),
        })
        require_native_ready(world.manifest)
        preview = world.path / "preview.png"
        with Image.open(preview) as image:
            image.verify()
        destination = temporary / "bundle"
        destination.mkdir()
        archive = world.export(destination / "world.zip")
        # Reopen the frozen export using the public integrity-checking SDK path.
        reopened = temporary / "reopened"
        with zipfile.ZipFile(archive) as package:
            package.extractall(reopened)
        loaded = load(reopened)
        require_native_ready(loaded.manifest)
        if loaded.id != world.id:
            raise ValueError("Frozen export did not retain the validated world identity")
        shutil.copyfile(preview, destination / "thumbnail.png")
        result = {**entry, "world_id": world.id, "bundle_sha256": file_digest(archive),
                  "bounds": list(spec.bounds), "components": len(spec.objects),
                  "source_instances": provenance["instances"],
                  "native_engine_version": world.manifest["runtime"]["engine_version"],
                  "thumbnail_view": thumbnail_view, "thumbnail_hidden_object_ids": hidden_ids,
                  "thumbnail_hidden_runtime_geoms": ["floor"],
                  "bundle_bytes": archive.stat().st_size}
        write_json(destination / "release.json", result)
        final = output / identifier
        if final.exists():
            shutil.rmtree(final)
        destination.rename(final)
    print(json.dumps({"starter_id": identifier, "world_id": result["world_id"],
                      "status": "ready", "components": result["components"],
                      "bundle_bytes": result["bundle_bytes"]}), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    args = parser.parse_args(argv)
    catalog = json.loads(args.catalog.read_text())
    if (len(catalog["environments"]) != 4
            or {entry["id"] for entry in catalog["environments"]} != {"warehouse", "house", "hospital", "bookstore"}):
        raise ValueError("The release requires all four pinned starter environments")
    # A prior catalog must not survive a failed new preparation attempt.
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "catalog.json").unlink(missing_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    entries, outcomes = [], []
    for entry in catalog["environments"]:
        try:
            result = freeze_one(entry, args.input / entry["id"], args.output, args.evidence)
            entries.append(result)
            outcome = {"starter_id": entry["id"], "status": "ready", "world_id": result["world_id"]}
        except Exception as exc:
            # Independent source scenes need independent diagnostics. One failed
            # reset must not hide issues in the remaining three environments.
            outcome = {"starter_id": entry["id"], "status": "failed", "error": public_error(exc)}
            print(json.dumps(outcome), flush=True)
        outcomes.append(outcome)
    write_json(args.evidence / "native-summary.json", {"environments": outcomes})
    failed = [outcome["starter_id"] for outcome in outcomes if outcome["status"] != "ready"]
    if failed:
        raise ValueError("Native starter preparation failed; no release catalog written: " + ", ".join(failed))
    write_json(args.output / "catalog.json", {"schema_version": 1, "environments": entries})


if __name__ == "__main__":
    main()
