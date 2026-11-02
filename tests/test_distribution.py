"""Archive checks catch omissions and accidental local data before release."""
import hashlib
import importlib.util
import io
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

CHECKER_PATH = Path(__file__).parents[1] / "tools/check_distribution.py"
module_spec = importlib.util.spec_from_file_location("check_distribution", CHECKER_PATH)
checker = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checker)

MANIPULATION_STARTERS = ("simready-kitchen", "simready-factory", "simready-living-room")
MANIPULATION_MEMBERS = (
    "world.zip", "thumbnail.png", "generation-plan.json", "task-suite.json",
    "semantic-plan.md", "preparation-evidence.json", "release.json",
)
MANIPULATION_MODULES_AND_CATALOGS = (
    "task_catalog.py", "joint_collisions.py", "simready_conveyor.py", "workbench_library.py",
    "living_room_library.py", "living_room_cabinet.py", "kitchen_counter_library.py", "kitchen_dish_library.py",
    "assets/simready/workbench_catalog.json", "assets/simready/living_room_catalog.json",
    "assets/simready/kitchen_counter_catalog.json", "assets/simready/kitchen_dish_catalog.json",
)


def archives(tmp_path, monkeypatch, *, wheel_change=None, source_extra=None, package_change=None):
    metadata = b"Metadata-Version: 2.4\nName: robot-worlds\nVersion: 0.1.0a1\n"
    files = {name: b"source document" for name in checker.REQUIRED_SOURCE_FILES}
    files["PKG-INFO"] = metadata
    package = {name: b"package resource" for name in checker.REQUIRED_PACKAGE_FILES}
    for collection, mesh in (("microwave079", "visuals/Body001.obj"), ("porcelain_bowl", "meshes/model.obj")):
        base = f"assets/household/{collection}/"
        package[base+"source-manifest.json"] = json.dumps({"files": [
            {"path": mesh, "sha256": hashlib.sha256(package[base+mesh]).hexdigest()}
        ]}).encode()
    base = "assets/household/porcelain_bowl/"
    package[base+"collision/part-000.obj"] = b"converted collision piece"
    package[base+"collision/manifest.json"] = json.dumps({
        "source_sha256": hashlib.sha256(package[base+"meshes/model.obj"]).hexdigest(),
        "parts": [{"file": "part-000.obj", "sha256": hashlib.sha256(package[base+"collision/part-000.obj"]).hexdigest()}],
    }).encode()
    if package_change:
        package_change(package)
    package["assets/robots/g1/CONTROLLER_LICENSE"] = b"pinned upstream license"
    monkeypatch.setattr(checker, "CONTROLLER_LICENSE_SHA256", hashlib.sha256(b"pinned upstream license").hexdigest())
    files.update({"src/robot_worlds/" + name: data for name, data in package.items()})
    wheel = {"robot_worlds/" + name: data for name, data in package.items()}
    wheel.update({"robot_worlds/THIRD_PARTY_NOTICES.md": files["THIRD_PARTY_NOTICES.md"],
                  "robot_worlds-0.1.0a1.dist-info/METADATA": metadata,
                  "robot_worlds-0.1.0a1.dist-info/licenses/LICENSE": b"project MIT license"})
    if wheel_change:
        wheel_change(wheel)
    if source_extra:
        files.update(source_extra)
    with zipfile.ZipFile(tmp_path / "robot_worlds-0.1.0a1-py3-none-any.whl", "w") as archive:
        for name, data in wheel.items():
            archive.writestr(name, data)
    with tarfile.open(tmp_path / "robot_worlds-0.1.0a1.tar.gz", "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo("robot_worlds-0.1.0a1/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_archive_pair_is_checked_without_checkout_import_or_extraction(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch)
    report = checker.inspect_distributions(tmp_path)
    assert report["status"] == "passed"
    assert report["version"] == "0.1.0a1"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("path", [".env", ".env.production", ".robot-worlds/private.json", "runs/run/trace.json", "../escape", "src/robot_worlds/__pycache__/sdk.pyc"])
def test_private_local_state_and_unsafe_paths_are_rejected(tmp_path, monkeypatch, path):
    archives(tmp_path, monkeypatch, source_extra={path: b"private content must never be logged"})
    with pytest.raises(checker.DistributionError) as caught:
        checker.inspect_distributions(tmp_path)
    assert "private content" not in str(caught.value)


@pytest.mark.parametrize("directory", sorted(checker.PREPARATION_DIRECTORIES))
@pytest.mark.parametrize("archive_kind", ["source", "wheel"])
def test_preparation_workspaces_cannot_leak_into_either_archive(
    tmp_path, monkeypatch, directory, archive_kind,
):
    content = b"intermediate native output or duplicate release artifact"
    path = f"{directory}/warehouse/scene.bin"
    if archive_kind == "source":
        archives(tmp_path, monkeypatch, source_extra={path: content})
    else:
        # A nested copy is also preparation data, rather than installed assets/starters/.
        path = "robot_worlds/" + path
        archives(tmp_path, monkeypatch, wheel_change=lambda files: files.update({path: content}))
    with pytest.raises(checker.DistributionError, match="Local state") as caught:
        checker.inspect_distributions(tmp_path)
    assert content.decode() not in str(caught.value)


def test_installed_starter_bundles_remain_in_both_checked_archives(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch)
    wheel = checker._archive_files(tmp_path / "robot_worlds-0.1.0a1-py3-none-any.whl")
    source = checker._archive_files(tmp_path / "robot_worlds-0.1.0a1.tar.gz")
    required = {f"assets/starters/{starter}/world.zip"
                for starter in ("warehouse", "house", "hospital", "bookstore")}
    required.update(f"assets/starters/{starter}/{member}"
                    for starter in MANIPULATION_STARTERS for member in MANIPULATION_MEMBERS)
    required.update(MANIPULATION_MODULES_AND_CATALOGS)
    assert required <= set(checker.REQUIRED_PACKAGE_FILES)
    for member in required:
        path = "robot_worlds/" + member
        assert wheel[path] == source["robot_worlds-0.1.0a1/src/" + path]
    assert checker.inspect_distributions(tmp_path)["status"] == "passed"


@pytest.mark.parametrize("member", MANIPULATION_MEMBERS)
@pytest.mark.parametrize("archive_kind", ["source", "wheel"])
def test_missing_manipulation_bundle_or_sidecar_is_rejected(tmp_path, monkeypatch, member, archive_kind):
    path = "assets/starters/simready-factory/" + member
    if archive_kind == "source":
        # Missing from both still fails completeness, rather than passing the
        # later wheel/source inventory equality check.
        archives(tmp_path, monkeypatch, package_change=lambda files: files.pop(path))
    else:
        archives(tmp_path, monkeypatch, wheel_change=lambda files: files.pop("robot_worlds/" + path))
    with pytest.raises(checker.DistributionError, match="missing required nonempty files") as caught:
        checker.inspect_distributions(tmp_path)
    assert path in str(caught.value)


@pytest.mark.parametrize("path", MANIPULATION_MODULES_AND_CATALOGS)
def test_missing_manipulation_module_or_source_catalog_is_rejected(tmp_path, monkeypatch, path):
    archives(tmp_path, monkeypatch, package_change=lambda files: files.pop(path))
    with pytest.raises(checker.DistributionError, match="missing required nonempty files") as caught:
        checker.inspect_distributions(tmp_path)
    assert path in str(caught.value)


@pytest.mark.parametrize("vcs_ignores_present", [False, True])
def test_build_selection_excludes_preparation_but_keeps_installed_starters(
    tmp_path, vcs_ignores_present,
):
    # Optional build backend: inspect its selection in a tiny fixture, never build
    # or scan the real conversion outputs. Also cover a source checkout without .gitignore.
    sdist = pytest.importorskip("hatchling.builders.sdist")
    wheel = pytest.importorskip("hatchling.builders.wheel")
    root = CHECKER_PATH.parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    config["tool"]["hatch"]["build"]["ignore-vcs"] = not vcs_ignores_present
    (tmp_path / "pyproject.toml").write_text((root / "pyproject.toml").read_text())
    if vcs_ignores_present:
        (tmp_path / ".gitignore").write_text((root / ".gitignore").read_text())
    paths = ["src/robot_worlds/__init__.py", "THIRD_PARTY_NOTICES.md"]
    paths += [f"{directory}/warehouse/unneeded.bin" for directory in checker.PREPARATION_DIRECTORIES]
    paths += [f"src/robot_worlds/assets/starters/{starter}/world.zip"
              for starter in ("warehouse", "house", "hospital", "bookstore")]
    paths += [f"src/robot_worlds/assets/starters/{starter}/{member}"
              for starter in MANIPULATION_STARTERS for member in MANIPULATION_MEMBERS]
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"tiny synthetic resource")
    for builder_type in (sdist.SdistBuilder, wheel.WheelBuilder):
        builder = builder_type(str(tmp_path), config=config)
        selected = {file.relative_path for file in builder.recurse_included_files()}
        assert all(not set(Path(name).parts) & checker.PREPARATION_DIRECTORIES for name in selected)
        assert {name for name in paths if name.startswith("src/")} <= selected


def test_missing_vendor_file_fails_before_clean_install(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch, wheel_change=lambda files: files.pop("robot_worlds/static/vendor/OBJLoader.js"))
    with pytest.raises(checker.DistributionError, match="missing required"):
        checker.inspect_distributions(tmp_path)


def test_wheel_cannot_silently_ship_different_source_bytes(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch, wheel_change=lambda files: files.update({"robot_worlds/sdk.py": b"different build source"}))
    with pytest.raises(checker.DistributionError, match="contents differ"):
        checker.inspect_distributions(tmp_path)


def test_changed_controller_license_requires_explicit_provenance_review(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch)
    monkeypatch.setattr(checker, "CONTROLLER_LICENSE_SHA256", "0" * 64)
    with pytest.raises(checker.DistributionError, match="pinned upstream LICENCE"):
        checker.inspect_distributions(tmp_path)


@pytest.mark.parametrize("member,reason", [
    ("meshes/model.obj", "Original household source"),
    ("collision/part-000.obj", "collision piece"),
])
def test_changed_source_or_collision_bytes_fail_even_when_both_archives_agree(tmp_path, monkeypatch, member, reason):
    archives(tmp_path, monkeypatch, package_change=lambda files: files.update({
        "assets/household/porcelain_bowl/"+member: b"modified geometry",
    }))
    with pytest.raises(checker.DistributionError, match=reason):
        checker.inspect_distributions(tmp_path)
