"""Inspect wheel/sdist completeness without extracting or importing the project."""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

CONTROLLER_LICENSE_SHA256 = "ff28cacaef7b9919a0fb905136cf4ccffba5191522bf5d024c4339932c0edc28"
REQUIRED_PACKAGE_FILES = (
    "__init__.py", "cli.py", "diagnostics.py", "sdk.py", "runtime.py", "g1_runtime.py",
    "static/index.html", "static/app.js", "static/styles.css", "static/scene.js",
    "static/scene-resources.js", "static/scene-inspection.js", "static/scene-appearance.js",
    "static/vendor/three.module.js", "static/vendor/three.core.js",
    "static/vendor/OBJLoader.js", "static/vendor/OrbitControls.js", "static/vendor/THREE-LICENSE.txt",
    "assets/robots/tiago/tiago_dual.xml", "assets/robots/tiago/tiago_dual_position.xml",
    "assets/robots/tiago/LICENSE", "assets/robots/tiago/PROVENANCE.md",
    "assets/robots/g1/g1_with_hands.xml", "assets/robots/g1/policy.onnx",
    "assets/robots/g1/deploy.yaml", "assets/robots/g1/LICENSE",
    "assets/robots/g1/CONTROLLER_LICENSE", "assets/robots/g1/DEX1_LICENSE",
    "assets/robots/g1/DEX1_UPSTREAM_LICENSE", "assets/robots/g1/DEX1_NOTICE",
    "assets/robots/g1/PROVENANCE.md",
    "asset_import.py", "asset_catalog.py", "asset_download.py", "asset_library.py", "sdf_import.py", "asset_sourcing.py", "prepared_assets.py", "mesh_assets.py", "static/scene-textures.js",
    "assets/household/microwave079/model.xml", "assets/household/microwave079/ATTRIBUTION.md",
    "assets/household/microwave079/source-manifest.json", "assets/household/microwave079/visuals/T_BC001.png",
    "assets/household/microwave079/visuals/Body001.obj", "assets/household/microwave079/visuals/door.obj",
    "assets/household/microwave079/visuals/door_Clear.obj", "assets/household/microwave079/visuals/Disc001.obj",
    "assets/household/porcelain_bowl/ATTRIBUTION.md", "assets/household/porcelain_bowl/source-manifest.json",
    "assets/household/porcelain_bowl/meshes/model.obj", "assets/household/porcelain_bowl/meshes/model.mtl",
    "assets/household/porcelain_bowl/materials/textures/texture.png",
    "assets/household/porcelain_bowl/metadata.pbtxt", "assets/household/porcelain_bowl/model.config",
    "assets/household/porcelain_bowl/collision/manifest.json",
)
REQUIRED_SOURCE_FILES = (
    "pyproject.toml", "uv.lock", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md", "SECURITY.md", "docs/setup.md", "docs/releasing.md", "PKG-INFO",
)
FORBIDDEN_PARTS = {".git", ".venv", ".robot-worlds", "__pycache__", "worlds", "runs"}
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class DistributionError(ValueError):
    pass


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or "\\" in name or ".." in path.parts:
        raise DistributionError("Unsafe archive member path")
    if any(part in FORBIDDEN_PARTS or part == ".env" or part.startswith(".env.") for part in path.parts):
        raise DistributionError("Local state or environment file found in archive")
    if path.suffix in {".pyc", ".pyo"} or path.name in {".DS_Store", "MUJOCO_LOG.TXT"}:
        raise DistributionError("Generated local file found in archive")
    return path


def _archive_files(path: Path) -> dict[str, bytes]:
    """Read regular files only, with size/path/link/duplicate checks."""
    result = {}
    size = 0

    def add(name, data):
        if name in result:
            raise DistributionError("Duplicate archive member")
        result[name] = data

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = str(_safe_name(member.filename))
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise DistributionError("Archive symlinks are not allowed")
                if member.is_dir():
                    continue
                size += member.file_size
                if size > MAX_ARCHIVE_BYTES:
                    raise DistributionError("Archive exceeds inspection size limit")
                add(name, archive.read(member))
    else:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = str(_safe_name(member.name))
                if member.isdir():
                    continue
                if not member.isfile():
                    raise DistributionError("Archive links or special files are not allowed")
                size += member.size
                if size > MAX_ARCHIVE_BYTES:
                    raise DistributionError("Archive exceeds inspection size limit")
                stream = archive.extractfile(member)
                if stream is None:
                    raise DistributionError("Unreadable archive member")
                add(name, stream.read())
    return result


def _require(files, names, kind):
    missing = [name for name in names if not files.get(name)]
    if missing:
        raise DistributionError(f"{kind} missing required nonempty files: " + ", ".join(missing))


def inspect_distributions(directory: str | Path) -> dict:
    """Check built artifacts, not checkout files or an editable installation."""
    directory = Path(directory)
    wheels = sorted(directory.glob("*.whl"))
    sources = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise DistributionError("Expected exactly one wheel and one .tar.gz source archive")
    wheel, source = _archive_files(wheels[0]), _archive_files(sources[0])
    roots = {PurePosixPath(name).parts[0] for name in source}
    if len(roots) != 1:
        raise DistributionError("Source archive must have one root directory")
    source = {str(PurePosixPath(name).relative_to(next(iter(roots)))): data for name, data in source.items()}
    _require(source, REQUIRED_SOURCE_FILES, "Source archive")
    _require(source, ("src/robot_worlds/" + name for name in REQUIRED_PACKAGE_FILES), "Source archive")
    _require(wheel, ("robot_worlds/" + name for name in REQUIRED_PACKAGE_FILES), "Wheel")
    _require(wheel, ("robot_worlds/THIRD_PARTY_NOTICES.md",), "Wheel")
    notices = source["THIRD_PARTY_NOTICES.md"]
    if wheel["robot_worlds/THIRD_PARTY_NOTICES.md"] != notices:
        raise DistributionError("Wheel third-party notices differ from the source archive")
    source_package = {name.removeprefix("src/"): data for name, data in source.items() if name.startswith("src/robot_worlds/")}
    wheel_package = {name: data for name, data in wheel.items() if name.startswith("robot_worlds/") and name != "robot_worlds/THIRD_PARTY_NOTICES.md"}
    if source_package.keys() != wheel_package.keys():
        raise DistributionError("Wheel package inventory differs from the source archive")
    if any(wheel_package[name] != data for name, data in source_package.items()):
        raise DistributionError("Wheel package contents differ from the source archive")
    for collection in ("microwave079", "porcelain_bowl"):
        base = f"robot_worlds/assets/household/{collection}/"
        original = json.loads(wheel[base+"source-manifest.json"])
        for record in original["files"]:
            if hashlib.sha256(wheel.get(base+record["path"], b"")).hexdigest() != record["sha256"]:
                raise DistributionError(f"Original household source file missing or changed: {collection}/{record['path']}")
    base = "robot_worlds/assets/household/porcelain_bowl/"
    conversion = json.loads(wheel[base+"collision/manifest.json"])
    if conversion["source_sha256"] != hashlib.sha256(wheel[base+"meshes/model.obj"]).hexdigest():
        raise DistributionError("Bowl collision conversion does not match the packaged source")
    for record in conversion["parts"]:
        if hashlib.sha256(wheel.get(base+"collision/"+record["file"], b"")).hexdigest() != record["sha256"]:
            raise DistributionError("Bowl collision piece is missing or changed")
    license_data = wheel["robot_worlds/assets/robots/g1/CONTROLLER_LICENSE"]
    if hashlib.sha256(license_data).hexdigest() != CONTROLLER_LICENSE_SHA256:
        raise DistributionError("G1 controller license differs from the pinned upstream LICENCE; review provenance before changing this check")
    metadata_files = [name for name in wheel if name.endswith(".dist-info/METADATA")]
    if len(metadata_files) != 1:
        raise DistributionError("Expected one wheel metadata record")
    wheel_metadata = BytesParser().parsebytes(wheel[metadata_files[0]])
    source_metadata = BytesParser().parsebytes(source["PKG-INFO"])
    if wheel_metadata["Name"] != "robot-worlds" or source_metadata["Name"] != "robot-worlds":
        raise DistributionError("Unexpected distribution name")
    if not wheel_metadata["Version"] or wheel_metadata["Version"] != source_metadata["Version"]:
        raise DistributionError("Wheel/source versions differ or are missing")
    if not any(".dist-info/licenses/" in name and name.endswith("LICENSE") for name in wheel):
        raise DistributionError("Wheel project license is missing")
    return {"status": "passed", "version": wheel_metadata["Version"],
            "package_files": len(wheel_package), "controller_license_sha256": CONTROLLER_LICENSE_SHA256,
            "artifacts": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "bytes": path.stat().st_size} for path in (wheels[0], sources[0])],
            "limit": "Archive completeness and pinned notice checks only; not native execution, model compatibility or a legal opinion."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="dist")
    args = parser.parse_args(argv)
    try:
        report = inspect_distributions(args.directory)
    except (DistributionError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
