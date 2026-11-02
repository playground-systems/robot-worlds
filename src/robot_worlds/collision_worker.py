"""Internal subprocess entry point for bounded collision preparation."""
import json
import sys
from pathlib import Path

from .collision_preparation import _native_worker

if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("This is an internal worker; use prepare_collision_mesh()")
    try:
        _native_worker(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as error:
        report = Path(sys.argv[2]).parent / "worker-error.json"
        report.write_text(json.dumps({"type": type(error).__name__, "message": str(error)[:500]}))
        raise SystemExit(1)
