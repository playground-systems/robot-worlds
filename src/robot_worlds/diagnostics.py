"""Offline setup checks. No network, native imports, rendering or file writes."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from importlib import metadata, resources
from pathlib import Path

DEPENDENCIES = (
    "robot-worlds", "mujoco", "numpy", "onnxruntime", "pydantic", "openai",
    "pillow", "fastapi", "uvicorn", "typer", "httpx",
)
REQUIRED_RESOURCES = (
    "static/index.html", "static/app.js", "static/styles.css", "static/scene.js",
    "static/scene-resources.js", "static/scene-inspection.js", "static/scene-appearance.js",
    "static/scene-assets.js", "static/scene-materials.js", "static/scene-textures.js", "static/scene-batching.js",
    "warehouse_library.py", "simready_structures.py", "assets/simready/catalog.json",
    "assets/codecs/decode-glb.mjs", "assets/codecs/meshopt_decoder.mjs",
    "assets/codecs/meshopt-source.json", "assets/codecs/MESHOPT-LICENSE.md",
    "static/vendor/three.module.js", "static/vendor/three.core.js",
    "static/vendor/OBJLoader.js", "static/vendor/OrbitControls.js", "static/vendor/THREE-LICENSE.txt",
    "assets/robots/tiago/tiago_dual.xml", "assets/robots/tiago/tiago_dual_position.xml",
    "assets/robots/tiago/LICENSE", "assets/robots/tiago/PROVENANCE.md",
    "assets/robots/g1/g1_with_hands.xml", "assets/robots/g1/policy.onnx",
    "assets/robots/g1/deploy.yaml", "assets/robots/g1/LICENSE",
    "assets/robots/g1/CONTROLLER_LICENSE", "assets/robots/g1/DEX1_LICENSE",
    "assets/robots/g1/DEX1_UPSTREAM_LICENSE", "assets/robots/g1/DEX1_NOTICE",
    "assets/robots/g1/PROVENANCE.md",
)


def collect_diagnostics(*, model: str = "gpt-6-astra", data_dir: str | Path = ".robot-worlds") -> dict:
    """Report offline prerequisites; success never implies model/graphics access.

    Dependency metadata and resource presence are inspected without importing
    MuJoCo, ONNX Runtime or OpenAI. pip check/native tests remain separate checks.
    No credential values, endpoint URLs or user paths enter the returned report.
    """
    checks = []
    versions = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    missing = [name for name, version in versions.items() if version is None]
    checks.append({"id": "dependencies", "status": "error" if missing else "ok",
                   "message": "Missing distributions: " + ", ".join(missing) if missing else "Required distribution metadata is present.",
                   "versions": versions,
                   "limit": "Presence does not validate version constraints or native library loading; run pip check and native tests."})
    checks.append({"id": "python", "status": "ok" if sys.version_info >= (3, 11) else "error",
                   "message": "Python 3.11 or later is required.",
                   "version": platform.python_version()})
    system = platform.system()
    checks.append({"id": "platform", "status": "ok" if system in {"Darwin", "Linux"} else "warning",
                   "message": "macOS and Linux are the documented platforms; Windows is not validated and the server uses POSIX locking.",
                   "system": system, "machine": platform.machine()})
    package = resources.files("robot_worlds")
    missing_resources = []
    for name in REQUIRED_RESOURCES:
        try:
            resource = package.joinpath(name)
            if not resource.is_file():
                missing_resources.append(name)
        except (OSError, ValueError):
            missing_resources.append(name)
    checks.append({"id": "package_resources", "status": "error" if missing_resources else "ok",
                   "message": "Required robot, license and browser files are present." if not missing_resources else "Required packaged files are missing; reinstall the complete wheel.",
                   "missing": missing_resources,
                   "limit": "Checks file presence only; asset content hashes are verified when worlds are loaded/evaluated."})
    key_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    checks.append({"id": "api_key", "status": "ok" if key_configured else "warning",
                   "configured": key_configured,
                   "message": "OPENAI_API_KEY is configured; access was not tested." if key_configured else "Set OPENAI_API_KEY before generation or LLM evaluation. Offline inspection and local policies do not need it."})
    checks.append({"id": "model", "status": "unverified", "model": model,
                   "message": "No API call was made. Verify this account's Responses, structured-output, image/function and input-token-counting access with an explicit bounded live smoke test.",
                   "pricing": "The default Astra price profile is pinned in code; alternate models require explicit verified rates or explicit SDK token-only mode."})
    backend = os.environ.get("MUJOCO_GL", "").strip().lower()
    # Never echo arbitrary environment values into shareable diagnostics.
    backend_label = backend if backend in {"egl", "glfw", "osmesa", "disable", "disabled", "off", "false", "0"} else "default" if not backend else "custom_value_not_shown"
    checks.append({"id": "graphics", "status": "warning" if backend == "egl" and system != "Linux" else "unverified",
                   "backend": backend_label,
                   "message": "EGL is supported by this project on compatible Linux hosts; macOS uses automatic local graphics. No graphics context, driver or GPU physics probe was run."})
    location = Path(data_dir).expanduser()
    try:
        ancestor = location
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        advisory_writable = ancestor.is_dir() and os.access(ancestor, os.W_OK)
    except (OSError, ValueError):
        advisory_writable = False
    checks.append({"id": "storage", "status": "ok" if advisory_writable else "warning",
                   "message": "The nearest existing data-directory ancestor appears writable." if advisory_writable else "Check write permissions for the chosen data directory.",
                   "limit": "Advisory permission check only; no directory or probe file was created."})
    node_available = shutil.which("node") is not None
    checks.append({"id": "warehouse_preparation", "status": "ok" if node_available else "warning",
                   "node_available": node_available,
                   "message": "Node is on PATH; its version was not probed. First warehouse preparation requires Node 20+." if node_available else
                              "Optional warehouse preparation needs Node 20+ for its first mesh decode. Existing prepared libraries and verified decoded caches do not require Node.",
                   "limit": "No executable was launched. This does not verify warehouse asset compatibility or native simulation."})
    return {"schema_version": "1", "mode": "offline", "offline_ready": not any(check["status"] == "error" for check in checks),
            "model_verified": False, "physics_verified": False, "graphics_verified": False,
            "checks": checks}


def format_diagnostics(report: dict, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2)
    lines = ["Robot Worlds offline setup check (no API, physics or graphics probes)"]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['id']}: {check['message']}")
    lines.append("Offline prerequisites passed." if report["offline_ready"] else "Offline prerequisites failed; fix the errors above.")
    lines.append("Model access, native execution and rendering remain unverified.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--data-dir", default=".robot-worlds")
    args = parser.parse_args(argv)
    report = collect_diagnostics(model=args.model, data_dir=args.data_dir)
    print(format_diagnostics(report, as_json=args.as_json))
    return 0 if report["offline_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
