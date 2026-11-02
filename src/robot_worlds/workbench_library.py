"""One pinned fixed packing table with retained visuals and addressable support.

Preparation checks geometry and provenance only. It does not simulate contacts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import numpy as np
from pydantic import TypeAdapter

from .appearance import PBR_RENDERER_NOTICE
from .asset_import import ImportedAsset
from .asset_library import AssetLibrary
from .asset_sourcing import AssetCandidate, AssetSearch, source_asset
from .gltf_decode import decode_glb
from .gltf_geometry import GLBGeometry
from .gltf_visuals import prepare_glb_visuals
from .models import Identifier, SceneObject, Vec3
from .simready_structures import component_envelopes

WORKBENCH_ID = "table_packing_heavyduty_c01"
_CATALOG = Path(__file__).parent / "assets/simready/workbench_catalog.json"
_MAX_DOWNLOAD = 5_000_000


def _catalog():
    return json.loads(_CATALOG.read_text())


def workbench_asset_search(query: str) -> AssetSearch:
    """Return the recorded bounded source comparison, without a fresh lookup."""
    if query != WORKBENCH_ID:
        raise ValueError("Only the reviewed fixed packing table is supported")
    catalog = _catalog()
    row = copy.deepcopy(catalog["selected"])
    candidate = AssetCandidate("simready:"+WORKBENCH_ID, row["optimized_url"],
        catalog["optimized_revision"], "CC-BY-4.0", metadata={**row,
            "upstream_checked_revision": catalog["upstream_checked_revision"],
            "derivation_upstream_revision": catalog["optimized_derivation_upstream_revision"]})
    return AssetSearch(query, tuple(catalog["catalogs"]), (candidate,), True,
        {"scope": "Recorded pinned source comparison; no new online lookup",
         "queries": catalog["queries"], "rejected_candidates": catalog["rejected"],
         "manifest_sha256": catalog["manifest_sha256"]})


def _allowed_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and (host == "huggingface.co" or host.endswith(".huggingface.co") or host.endswith(".hf.co")))


def _source(candidate, cache, *, check, remaining):
    """Fetch at most one 5 MB pinned GLB; cache hits recheck its bytes."""
    row = candidate.metadata
    size, expected = row["download_bytes"], row["download_sha256"]
    if (not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= _MAX_DOWNLOAD
            or not isinstance(expected, str) or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
            or not _allowed_url(candidate.source_url)):
        raise ValueError("Invalid pinned workbench source")
    cache.mkdir(parents=True, exist_ok=True)
    glb = cache / "model.glb"

    def verify(path):
        check()
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError("Workbench source cache is incomplete or changed")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Workbench source checksum mismatch")

    if glb.exists() or glb.is_symlink():
        verify(glb)
    else:
        with tempfile.TemporaryDirectory(prefix=".workbench-", dir=cache) as scratch:
            staged = Path(scratch) / "model.glb"
            url = candidate.source_url
            try:
                with httpx.Client(follow_redirects=False, trust_env=False) as client:
                    for redirect in range(6):
                        check()
                        with client.stream("GET", url, timeout=remaining(), follow_redirects=False) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location", "")
                                url = urljoin(url, location)
                                if not location or redirect == 5 or not _allowed_url(url):
                                    raise ValueError("Unsupported workbench source redirect")
                                continue
                            if response.status_code != 200:
                                raise ValueError(f"Workbench source returned HTTP {response.status_code}")
                            count = 0
                            with staged.open("xb") as output:
                                for chunk in response.iter_bytes():
                                    check()
                                    count += len(chunk)
                                    if count > size:
                                        raise ValueError("Workbench source exceeds its pinned size")
                                    output.write(chunk)
                            break
            except httpx.HTTPError as error:
                raise ValueError(f"Workbench request failed ({type(error).__name__})") from None
            verify(staged)
            staged.rename(glb)
    notice = (f"# {row.get('asset_id', WORKBENCH_ID)}\n\nOriginal model: NVIDIA SimReady.\n"
        "Optimized GLB conversion: CodePawl. License: CC BY 4.0.\n"
        f"Source: {candidate.source_url}\n"
        f"Official package: {row['upstream_packaging_url']}\n"
        +row.get("conversion_notice", "Robot Worlds retains source meshes/material maps and adapts collision to fixed component boxes.\n")
        +"No native contact or task-success validation has been performed.\n\n"
        +row["upstream_license_text"])
    metadata = {"schema_version": 1, "source_url": candidate.source_url,
        "revision": candidate.revision, "license": candidate.license, "source": row,
        "status": "source_only_not_physically_validated"}
    for name, encoded in (("NOTICE.md", notice.encode()),
            ("source.json", (json.dumps(metadata, indent=2)+"\n").encode())):
        path = cache / name
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
                raise ValueError("Workbench provenance cache was changed")
        else:
            path.write_bytes(encoded)
    return glb, cache / "NOTICE.md", metadata


def workbench_geometry(primitives, *, check=lambda: None):
    """Conservative connected-component envelopes preserve the leg openings."""
    boxes = component_envelopes(primitives, check=check)
    low = np.min([a for a, _ in boxes], axis=0)
    high = np.max([b for _, b in boxes], axis=0)
    if not np.allclose(high-low, [1.86689937, 1.21937865, .99398561], atol=.002, rtol=0):
        raise ValueError("Packing table dimensions differ from the reviewed source")
    supports = [i for i, (a, b) in enumerate(boxes)
        if b[0]-a[0] > 1.8 and b[1]-a[1] > 1.2 and .04 < b[2]-a[2] < .05
        and abs(b[2]-high[2]) < .001]
    if len(supports) != 1:
        raise ValueError("Packing table needs one addressable source tabletop")
    # The real source has a low shelf. This clearance lies above that shelf,
    # between the legs and below the upper frame, rather than filling the table.
    clearance_low = np.array([-.75, -.28, .40])
    clearance_high = np.array([.75, .28, .85])
    for a, b in boxes:
        if np.all(np.minimum(clearance_high, b)-np.maximum(clearance_low, a) > 1e-6):
            raise ValueError("Packing table collision fills its reviewed under-table clearance")
    top = supports[0]
    return [boxes[top], *[box for i, box in enumerate(boxes) if i != top]], {
        "low": clearance_low.tolist(), "high": clearance_high.tolist()}


def import_workbench(cache_dir: str | Path, *, prefix="workbench", position=(0, 0, 0),
                     timeout=120, cancelled=None):
    """Prepare the fixed source table; expose geom_ids['tabletop'] for placement."""
    TypeAdapter(Identifier).validate_python(prefix+"_collision255")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Workbench preparation timeout must be in (0, 120]")
    cache = Path(cache_dir).expanduser().resolve()
    deadline = time.monotonic()+timeout

    def check():
        if cancelled and cancelled():
            raise ValueError("Workbench preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Workbench preparation exceeded its time budget")

    def remaining():
        check()
        return deadline-time.monotonic()

    def adapt(candidate):
        check()
        glb, notice, metadata = _source(candidate, cache, check=check, remaining=remaining)
        decoded = decode_glb(glb, cache / "decoded", timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        doc = geometry.document
        if (doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsJoints")
                or any(n.get("extras", {}).get("joints") or
                    "joint" in n.get("extensions", {}).get("KHR_physics_rigid_bodies", {})
                    for n in doc.get("nodes", []))):
            raise ValueError("A jointed source cannot become the reviewed rigid packing table")
        motions = [n.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("motion")
            for n in doc.get("nodes", [])]
        motions = [m for m in motions if m is not None]
        materials = doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsMaterials", [])
        if len(motions) != 1 or len(materials) != 1:
            raise ValueError("Packing table source physics changed")
        mass, friction = motions[0].get("mass"), materials[0].get("dynamicFriction")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (mass, friction)) or mass <= 0 or friction < 0:
            raise ValueError("Packing table requires finite source mass and friction")
        primitives = geometry.primitives()
        boxes, clearance = workbench_geometry(primitives, check=check)
        visuals = prepare_glb_visuals(decoded.path, cache / "visuals", source_notice=notice,
            source_metadata=metadata, timeout=remaining(), cancelled=cancelled)
        low = np.min([a for a, _ in boxes], axis=0)
        high = np.max([b for _, b in boxes], axis=0)
        offset = np.array(position)-np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        limitations = ["Fixed packing table: each connected source component uses a conservative box; small holes and curved detail may be filled.",
            "The source low shelf is retained; reviewed open space above it remains free of collision boxes.",
            "Source mass is distributed by proxy volume; source inertia, center of mass and restitution are not retained.",
            "Original detailed meshes and material maps remain separate appearance geometry.",
            "Geometric checks only; no native contact, reachability, simulation or task-success validation.", PBR_RENDERER_NOTICE]
        recipe = {"schema_version": 1, "profile": "packing-table-component-boxes-v1",
            "asset_id": WORKBENCH_ID, "source": metadata, "source_total_mass_kg": mass,
            "source_dynamic_friction": friction, "mobility": "explicitly fixed work surface",
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "weld_grid_metres": .00001,
            "components": [{"low": a.tolist(), "high": b.tolist()} for a, b in boxes],
            "support_components": {"tabletop": 0, "work_surface": 0},
            "under_table_clearance": clearance, "limitations": limitations}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_path = cache / "workbench-physics.json"
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Workbench physics provenance changed")
        else:
            recipe_path.write_bytes(encoded)
        asset = ImportedAsset(source_files=visuals.source_files, disclosures=limitations)
        asset.source_files.update({"optimized-source.glb": candidate.metadata["download_sha256"],
            "upstream-license.txt": candidate.metadata["upstream_license_sha256"],
            "upstream-packaging.json": candidate.metadata["upstream_packaging_sha256"],
            "workbench-physics.json": hashlib.sha256(encoded).hexdigest()})
        root = prefix+"_tabletop"
        volumes = np.array([np.prod(b-a) for a, b in boxes])
        for i, ((a, b), volume) in enumerate(zip(boxes, volumes)):
            asset.objects.append(SceneObject(id=root if i == 0 else f"{prefix}_collision{i}",
                label="Source packing tabletop" if i == 0 else "Source packing table component envelope",
                kind="box", position=(a+b)/2+offset, size=b-a, movable=False, visible=False,
                parent_id=None if i == 0 else root, mass=mass*volume/volumes.sum(), friction=friction,
                source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
        for obj in visuals.objects(prefix=prefix, parents={p.node_index: root for p in primitives}):
            obj.position = np.array(obj.position)+offset
            asset.objects.append(obj)
        asset.body_ids["fixed_workbench"] = root
        asset.geom_ids.update({"tabletop": root, "work_surface": root})
        check()
        return asset

    def unavailable(*args):
        raise ValueError("Workbench source import unresolved; procedural replacement is not a fallback")

    return source_asset(WORKBENCH_ID, search=workbench_asset_search, import_asset=adapt, create_asset=unavailable)


def prepare_workbench_library(destination: str | Path, *, cache_dir: str | Path | None = None,
                              timeout=120, cancelled=None) -> Path:
    """Save one portable source-backed library, without replacing existing output."""
    destination = Path(destination).expanduser()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Workbench library destination already exists")
    destination = destination.resolve()
    cache = Path(cache_dir).expanduser().resolve() if cache_dir is not None else destination.parent / "workbench-preparation"
    if cache.is_relative_to(destination):
        raise ValueError("Workbench cache must be outside the library destination")
    sourced = import_workbench(cache, timeout=timeout, cancelled=cancelled)
    library = AssetLibrary().add(WORKBENCH_ID, sourced,
        description="Fixed NVIDIA packing/work table with addressable tabletop and open space above its low shelf; original visuals; native contact validation pending")
    return library.save(destination)
