"""Native-validate prepared source scenes and package offline starter bundles.

Run on a Linux release worker under Xvfb, after ``prepare.py``. No model calls,
upstream code, or customer-time conversion are involved. A failed native check
leaves diagnostics and never produces a release catalog claiming availability.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from robot_worlds.artifacts import file_digest, inside, write_json
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


def overview_runtime(spec, **kwargs):
    """Use the ordinary native reset probe with a scene overview as its preview."""
    from robot_worlds.runtime import Runtime

    class OverviewRuntime(Runtime):
        def render(self):
            bounds = self.spec.bounds
            distance = math.hypot(bounds[0], bounds[1]) * .85
            return super().render(view="overview", azimuth=135, elevation=-65,
                                  distance=max(4, distance), lookat=[0, 0, min(1, bounds[2] / 3)])

    return OverviewRuntime(spec, **kwargs)


def freeze_one(entry: dict, source: Path, output: Path, evidence: Path) -> dict:
    identifier = entry["id"]
    provenance = verify_prepared(source, identifier)
    for key in ("id", "repo", "revision", "world", "license", "source_url"):
        if provenance.get(key) != entry.get(key):
            raise ValueError(f"Prepared provenance does not match pinned catalog: {key}")
    spec = WorldSpec.model_validate_json((source / "spec.json").read_text())
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
                  "thumbnail_view": "Native MuJoCo source-scene overview at reset",
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    # A prior catalog must not survive a failed new preparation attempt.
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "catalog.json").unlink(missing_ok=True)
    entries = [freeze_one(entry, args.input / entry["id"], args.output, args.evidence)
               for entry in catalog["environments"]]
    if len(entries) != 4 or {entry["id"] for entry in entries} != {"warehouse", "house", "hospital", "bookstore"}:
        raise ValueError("The release requires all four pinned starter environments")
    write_json(args.output / "catalog.json", {"schema_version": 1, "environments": entries})


if __name__ == "__main__":
    main()
