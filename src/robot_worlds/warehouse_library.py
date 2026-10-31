"""Prepare the reviewed source subset for SDK and self-hosted generation."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .asset_library import AssetLibrary
from .simready_structures import import_simready_structure

WAREHOUSE_ASSETS = {
    "rack_large_empty_a02": "Fixed rack with three open shelf regions",
    "pallet_a01": "Movable wooden pallet with four open fork channels",
    "box_corrugated_brown_b20": "Movable sealed rigid carton; no flap opening or cardboard deformation",
    "barrier_fence_metal_a02": "Fixed safety barrier with two open rail gaps",
}


@dataclass(frozen=True)
class PreparedWarehouseLibrary:
    path: Path
    asset_ids: tuple[str, ...]
    seconds: float


def prepare_warehouse_library(destination: str | Path, *, cache_dir: str | Path | None = None,
                              asset_ids: Sequence[str] | None = None, timeout: float = 300,
                              cancelled: Callable[[], bool] | None = None,
                              on_event: Callable[[dict], None] | None = None) -> PreparedWarehouseLibrary:
    """Prepare selected pinned sources, then atomically publish a new library.

    No scene, policy, model call or native simulation is created. Failed/cancelled
    preparation retains verified caches but publishes no partial library. Existing
    destinations are never replaced. Timeout/cancellation checks are cooperative;
    the final bounded library copy is the commit step, interrupted by Ctrl-C.
    """
    requested = tuple(WAREHOUSE_ASSETS) if asset_ids is None else tuple(asset_ids)
    if (isinstance(asset_ids, str) or not requested or len(requested) > len(WAREHOUSE_ASSETS)
            or any(not isinstance(value, str) or value not in WAREHOUSE_ASSETS for value in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError("Choose distinct reviewed warehouse asset IDs: " + ", ".join(WAREHOUSE_ASSETS))
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Warehouse preparation timeout must be finite and in (0, 600]")
    destination = Path(destination).expanduser()
    if destination.is_symlink() or destination.exists():
        raise ValueError("Asset library destination already exists; use it as-is or choose a new --output directory")
    destination = destination.resolve()
    cache = Path(cache_dir).expanduser().resolve() if cache_dir is not None else destination.parent / "asset-cache"
    if cache.is_relative_to(destination):
        raise ValueError("Asset cache must be outside the new library destination")
    started = time.monotonic()
    deadline = started+timeout
    emit = on_event or (lambda _: None)

    def check():
        if cancelled and cancelled():
            raise ValueError("Warehouse preparation cancelled; verified caches can be reused")
        if time.monotonic() >= deadline:
            raise ValueError("Warehouse preparation exceeded its time budget; verified caches can be reused")

    library = AssetLibrary()
    for index, asset_id in enumerate(requested):
        check()
        emit({"type": "asset_preparing", "asset_id": asset_id, "index": index+1, "total": len(requested)})
        check()
        sourced = import_simready_structure(asset_id, cache, timeout=min(120, deadline-time.monotonic()), cancelled=cancelled)
        check()
        library.add(asset_id, sourced, description=WAREHOUSE_ASSETS[asset_id]+"; conservative collision proxies; native contact validation pending")
        emit({"type": "asset_prepared", "asset_id": asset_id, "index": index+1, "total": len(requested),
              "components": len(sourced.asset.objects), "license": sourced.selected.license})
    check()
    emit({"type": "library_saving", "assets": len(requested)})
    check()
    path = library.save(destination)
    return PreparedWarehouseLibrary(path, requested, time.monotonic()-started)
