"""Mirror actual remote spectator events into a local read-only workbench job."""
import base64
import re
import uuid
from pathlib import Path

from robot_worlds.artifacts import utc_now, write_json


class LiveMirror:
    def __init__(self, root):
        self.root = Path(root)
        self.id = uuid.uuid4().hex
        self.path = self.root / "jobs" / self.id / "job.json"
        self.job = {"id": self.id, "kind": "evaluate", "status": "running",
                    "created_at": utc_now(), "events": [], "transport": "remote_native_ssh"}
        self.save()

    @property
    def cancelled(self):
        return (self.path.parent / "cancel").exists()

    def save(self):
        write_json(self.path, self.job)

    def event(self, event):
        event = dict(event)
        run = event.get("run_id")
        if run is not None:
            if not re.fullmatch(r"[a-f0-9]{32}", run):
                raise ValueError("Invalid live run ID")
            self.job["run_id"] = run
            folder = self.root / "runs" / run
            if not (folder / "result.json").exists():
                write_json(folder / "result.json", {"id": run, "status": "running", "episodes": [],
                                                    "transport": "live preview; full evidence transfers after execution"})
        if event.get("type") == "live_frame":
            member = event.get("path", "")
            if not run or not re.fullmatch(r"episode-\d{3}/live\.png", member):
                raise ValueError("Invalid live frame path")
            pixels = base64.b64decode(event.pop("image_base64"), validate=True)
            if len(pixels) > 4*1024*1024:
                raise ValueError("Live frame is too large")
            target = folder / member
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(pixels)
            temporary.replace(target)
            self.job["live_frame"] = event
        else:
            self.job["events"] = [*self.job["events"], event][-200:]
        self.save()

    def finish(self, status, error=None):
        self.job["status"] = "cancelled" if self.cancelled else status
        self.job["finished_at"] = utc_now()
        if error:
            self.job["error"] = error
        self.save()
