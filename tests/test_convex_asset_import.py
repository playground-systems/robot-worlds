import hashlib
import json

import pytest
from test_mesh_assets import cube

from robot_worlds import import_convex_mesh_asset


def write_obj(path, vertices, faces):
    path.write_text("\n".join(["v "+" ".join(map(str, v)) for v in vertices]
                             + ["f "+" ".join(str(int(i)+1) for i in f) for f in faces])+"\n")


@pytest.fixture
def converted(tmp_path):
    v, f = cube()
    v *= .2
    visual = tmp_path / "original.obj"
    write_obj(visual, v, f)
    folder = tmp_path / "collision"
    folder.mkdir()
    pieces = []
    for i in range(2):
        part = v.copy()
        part[:, 0] = part[:, 0]/2+i*.1
        path = folder / f"part-{i}.obj"
        write_obj(path, part, f)
        pieces.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "volume_m3": 999})  # Import recomputes volume from geometry.
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "source_sha256": hashlib.sha256(visual.read_bytes()).hexdigest(), "parts": pieces}))
    notice = tmp_path / "NOTICE.txt"
    notice.write_text("Synthetic cube fixture authored for this test; not a sourced product asset.")
    return dict(mesh_path=visual, collision_manifest=manifest, prefix="object", mass=.3,
                source_notice=notice, position=(1, 2, 3))


def test_mass_placement_appearance_and_provenance_survive_compound_import(converted):
    original = converted["mesh_path"].read_bytes()
    asset = import_convex_mesh_asset(**converted)
    root, second, visual = asset.objects
    assert root.movable and root.parent_id is None
    assert not second.movable and second.parent_id == root.id
    assert root.mass == pytest.approx(.15)
    assert second.mass == pytest.approx(.15)
    assert root.position == pytest.approx((1.05, 2.1, 3.1))
    assert second.position == pytest.approx((1.15, 2.1, 3.1))
    assert visual.position == pytest.approx((1.1, 2.1, 3.1))
    assert visual.visible and not visual.collision
    assert not root.visible and not second.visible
    assert asset.body_ids["object"] == root.id
    assert converted["mesh_path"].read_bytes() == original
    assert "collision/manifest.json" in asset.source_files


@pytest.mark.parametrize("damage", ["source", "part", "concave", "path", "duplicate", "empty"])
def test_unverified_or_mismatched_components_cannot_be_imported(converted, damage):
    path = converted["collision_manifest"]
    manifest = json.loads(path.read_text())
    part = path.parent / manifest["parts"][0]["file"]
    if damage == "source":
        manifest["source_sha256"] = "wrong"
    elif damage == "part":
        part.write_text(part.read_text()+"# changed\n")
    elif damage == "concave":
        v, f = cube()
        v[6] = (.4, .4, .4)
        write_obj(part, v*.1, f)
        manifest["parts"][0]["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
    elif damage == "path":
        manifest["parts"][0]["file"] = "../original.obj"
    elif damage == "duplicate":
        manifest["parts"][1] = manifest["parts"][0]
    else:
        manifest["parts"] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        import_convex_mesh_asset(**converted)
