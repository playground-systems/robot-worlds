"""Exercise real bundled starters through the public API and SDK, without physics.

Run after native release preparation has installed all four bundles. The normal
spawned job worker verifies/unpacks the real archives. This does not run a robot,
revalidate on the current installation, or make model calls.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from robot_worlds import add_starter, load
from robot_worlds.server import create_app


def check_installed(workspace: Path) -> dict:
    os.environ["OPENAI_API_KEY"] = ""
    records = []
    with TestClient(create_app(workspace)) as client:
        assert client.get("/api/health").json()["key_configured"] is False
        catalog = client.get("/api/starters").json()
        assert [entry["id"] for entry in catalog] == ["warehouse", "house", "hospital", "bookstore"]
        assert all(entry["available"] and entry["preview_available"] for entry in catalog)
        for entry in catalog:
            preview = client.get(f"/api/starters/{entry['id']}/preview")
            assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG\r\n\x1a\n")
            started = time.perf_counter()
            response = client.post(f"/api/starters/{entry['id']}/add")
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            deadline = time.monotonic() + 120
            while True:
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] not in {"queued", "running"}:
                    break
                assert time.monotonic() < deadline, f"Starter job timed out: {entry['id']}"
                time.sleep(.05)
            elapsed = time.perf_counter() - started
            assert job["status"] == "completed", job
            assert job["world_id"] == entry["world_id"]
            response = client.get(f"/api/worlds/{job['world_id']}")
            assert response.status_code == 200, response.text
            world = response.json()
            assert world["manifest"]["validation"]["status"] == "ready"
            assert world["manifest"]["runtime"]["engine"] == "mujoco"
            assert len(world["spec"]["objects"]) == entry["components"]
            assert world["manifest"]["id"] == entry["world_id"]
            # Repeat through the public SDK; this checks the existing immutable
            # version instead of extracting a second scene or rebuilding assets.
            repeat_started = time.perf_counter()
            reopened = add_starter(entry["id"], output=workspace / "worlds")
            assert reopened.id == entry["world_id"] == load(reopened.path).id
            records.append({"id": entry["id"], "world_id": reopened.id,
                            "components": len(reopened.spec.objects),
                            "bundle_bytes": entry["bundle_bytes"],
                            "api_add_seconds": round(elapsed, 3),
                            "sdk_reopen_seconds": round(time.perf_counter() - repeat_started, 3),
                            "native_validation": reopened.manifest["validation"]["status"],
                            "requires_native_revalidation": world["runtime_compatibility"]["requires_native_revalidation"]})
        assert len(client.get("/api/worlds").json()) == 4
    # Reopening the application must retain all four immutable worlds.
    with TestClient(create_app(workspace)) as client:
        assert len(client.get("/api/worlds").json()) == 4
        for record in records:
            response = client.get(f"/api/worlds/{record['world_id']}")
            assert response.status_code == 200
            assert response.json()["manifest"]["id"] == record["world_id"]
    return {"status": "passed", "model_calls": 0, "native_runs": 0,
            "worker": "production spawned process", "application_reopen": "passed",
            "environments": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.workspace:
        report = check_installed(args.workspace.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="robot-worlds-starter-check-") as temporary:
            report = check_installed(Path(temporary))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
