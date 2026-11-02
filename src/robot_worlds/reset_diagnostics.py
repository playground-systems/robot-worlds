"""Read-only evidence from the existing simulator initialization rollout."""

from __future__ import annotations

import copy
import math

MAX_CONTACT_PAIRS = 2000
INITIAL_PENETRATION_LIMIT_M = 0.001
PEAK_EXCURSION_LIMIT_M = 0.05
INVALIDATING_WARNINGS = frozenset({
    "mjWARN_BADQPOS", "mjWARN_BADQVEL", "mjWARN_BADQACC", "mjWARN_BADCTRL",
    "mjWARN_CONTACTFULL", "mjWARN_CNSTRFULL",
})


class ResetObserver:
    """Observe existing steps without changing controls, live state, or timing."""

    def __init__(self, runtime):
        import mujoco
        import numpy as np

        self.mj, self.np = mujoco, np
        self.runtime, self.model, self.data = runtime, runtime.model, runtime.data
        self.started = float(self.data.time)
        self.contacts, self.robot_contacts, self.objects = {}, {}, {}
        self.warning_counts = [0] * len(self.data.warning)
        self.finite = True
        self.complete = True
        self.samples = 0
        self._tracked = {}
        for obj in runtime.spec.objects:
            bid = runtime._object_bodies[obj.id]
            if self.model.body_weldid[bid] == 0:
                continue
            free_joint = None
            for jid in range(self.model.body_jntadr[bid], self.model.body_jntadr[bid]+self.model.body_jntnum[bid]):
                if self.model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_FREE:
                    free_joint = jid
            self._tracked[obj.id] = (bid, free_joint)
            position = self.data.xpos[bid].copy()
            self.objects[obj.id] = {
                "initial_position": position.tolist(), "final_position": position.tolist(),
                "peak_excursion_m": 0.0, "peak_excursion_time": 0.0,
                "peak_linear_speed_m_s": 0.0, "peak_speed_time": 0.0,
                "free_body": free_joint is not None,
                "speed_measurement": "free-joint linear velocity" if free_joint is not None else "center displacement per physics timestep",
            }
        self.scratch = mujoco.MjData(self.model) if any(j is None for _, j in self._tracked.values()) else None
        self.sample(initial=True)

    def _entity(self, gid):
        oid = self.runtime._geom_objects.get(gid)
        if oid is not None:
            return {"kind": "scene_object", "id": oid}
        name = self.mj.mj_id2name(self.model, self.mj.mjtObj.mjOBJ_GEOM, gid)
        return {"kind": "floor" if name == "floor" else "robot", "id": name or f"geom_{gid}"}

    def sample(self, *, initial=False):
        np = self.np
        t = float(self.data.time)-self.started
        self.samples += 1
        self.finite &= bool(np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all())
        for i, warning in enumerate(self.data.warning):
            self.warning_counts[i] = max(self.warning_counts[i], int(warning.number))
        if self.scratch is not None:
            self.scratch.qpos[:] = self.data.qpos
            self.mj.mj_kinematics(self.model, self.scratch)
        for oid, (bid, free_joint) in self._tracked.items():
            record = self.objects[oid]
            if free_joint is not None:
                qa, va = self.model.jnt_qposadr[free_joint], self.model.jnt_dofadr[free_joint]
                position = self.data.qpos[qa:qa+3]
                speed = float(np.linalg.norm(self.data.qvel[va:va+3]))
            else:
                position = self.scratch.xpos[bid]
                speed = 0.0 if initial else float(np.linalg.norm(position-record["final_position"])/self.model.opt.timestep)
            if not np.isfinite(position).all() or not math.isfinite(speed):
                self.finite = False
                continue
            excursion = float(np.linalg.norm(position-record["initial_position"]))
            if excursion > record["peak_excursion_m"]:
                record.update(peak_excursion_m=excursion, peak_excursion_time=t)
            if speed > record["peak_linear_speed_m_s"]:
                record.update(peak_linear_speed_m_s=speed, peak_speed_time=t)
            record["final_position"] = position.tolist()
        # mj_step integrates after collision computation. Its contact cache is
        # the preceding pre-integration state; retain that timestamp explicitly.
        contact_time = t if initial else max(0.0, t-float(self.model.opt.timestep))
        for contact in self.data.contact[:self.data.ncon]:
            if not math.isfinite(float(contact.dist)):
                self.finite = False
                continue
            if contact.dist >= 0:
                continue
            a, b = sorted((int(contact.geom1), int(contact.geom2)))
            first, second = self._entity(a), self._entity(b)
            if "scene_object" not in (first["kind"], second["kind"]):
                continue  # Robot-floor and robot self-contacts are not scene failures.
            if all(self.model.body_weldid[self.model.geom_bodyid[g]] == 0 for g in (a, b)):
                continue  # Preserve fixed-reference welded assembly exemptions.
            collection = self.robot_contacts if "robot" in (first["kind"], second["kind"]) else self.contacts
            key = (a, b)
            if key not in collection:
                if len(self.contacts)+len(self.robot_contacts) >= MAX_CONTACT_PAIRS:
                    self.complete = False
                    continue
                collection[key] = {"entities": [first, second], "initial_penetration_m": 0.0,
                                   "peak_penetration_m": 0.0, "peak_time": contact_time}
            record = collection[key]
            depth = -float(contact.dist)
            if initial:
                record["initial_penetration_m"] = max(record["initial_penetration_m"], depth)
            if depth > record["peak_penetration_m"]:
                record.update(peak_penetration_m=depth, peak_time=contact_time)

    def finish(self):
        return {
            "version": 1, "complete": self.complete, "finite_state": self.finite,
            "duration_seconds": float(self.data.time)-self.started,
            "sample_count": self.samples, "physics_timestep": float(self.model.opt.timestep),
            "objects": copy.deepcopy(self.objects),
            "scene_contacts": [copy.deepcopy(self.contacts[k]) for k in sorted(self.contacts)],
            "scene_robot_contacts": [copy.deepcopy(self.robot_contacts[k]) for k in sorted(self.robot_contacts)],
            "warnings": {self.mj.mjtWarning(i).name: count for i, count in enumerate(self.warning_counts) if count},
            "max_contact_pairs": MAX_CONTACT_PAIRS,
            "limitations": [
                "Observes the existing initialization rollout; adds no simulator steps or control changes.",
                "Scene centers use current free-joint positions or kinematics on separate scratch data; contacts use the preceding pre-integration solver state and its timestamp.",
                "Only active solver contacts are measured; welded and other solver-filtered overlaps are not certified collision-free.",
                "Robot-floor/self contacts are excluded. Scene-robot contacts are informational, not a robot initialization rejection rule.",
                "Peak speed and transient penetration are evidence, not rejection thresholds; compliant landings can produce temporary contact penetration.",
            ],
        }


def settling_check(evidence):
    """Build a repair-oriented check with explicit, versioned acceptance limits."""
    check = {"check": "physics_settling", "passed": True, "errors": [], "issues": [],
             "thresholds": {"initial_scene_penetration_m": INITIAL_PENETRATION_LIMIT_M,
                            "peak_free_object_excursion_m": PEAK_EXCURSION_LIMIT_M,
                            "invalidating_warning_names": sorted(INVALIDATING_WARNINGS)},
             "evidence": copy.deepcopy(evidence)}
    if evidence is None:
        check.update(status="unavailable", evidence=None)
        return check  # Custom runtimes retain their existing final-state checks.
    if not evidence.get("complete") or not evidence.get("finite_state"):
        check["errors"].append("Reset/settling diagnostics are incomplete or contain non-finite simulator state")
    for name, count in evidence.get("warnings", {}).items():
        if name in INVALIDATING_WARNINGS and count:
            check["issues"].append({"kind": "simulator_warning", "warning": name, "count": count})
            check["errors"].append(f"Settling emitted {name} ({count}); numerical state or contact evidence is unreliable")
    for contact in evidence.get("scene_contacts", []):
        depth = contact["initial_penetration_m"]
        if depth > INITIAL_PENETRATION_LIMIT_M:
            ids = [entity["id"] for entity in contact["entities"]]
            check["issues"].append({"kind": "initial_penetration", "entities": contact["entities"],
                                    "measured_m": depth, "limit_m": INITIAL_PENETRATION_LIMIT_M})
            check["errors"].append(f"{'/'.join(ids)}: initial scene penetration {depth:.6f}m exceeds {INITIAL_PENETRATION_LIMIT_M}m")
    for oid, state in evidence.get("objects", {}).items():
        excursion = state["peak_excursion_m"]
        if state["free_body"] and excursion > PEAK_EXCURSION_LIMIT_M:
            check["issues"].append({"kind": "transient_excursion", "object_id": oid,
                                    "measured_m": excursion, "limit_m": PEAK_EXCURSION_LIMIT_M,
                                    "time": state["peak_excursion_time"],
                                    "initial_position": state["initial_position"],
                                    "final_position": state["final_position"]})
            check["errors"].append(f"{oid}: peak settling excursion {excursion:.6f}m exceeds existing {PEAK_EXCURSION_LIMIT_M}m placement limit")
    check["passed"] = not check["errors"]
    check["status"] = "passed" if check["passed"] else "failed"
    return check
