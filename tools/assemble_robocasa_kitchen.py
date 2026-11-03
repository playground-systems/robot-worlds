"""Compose selected source kitchen XML without a native simulator or robot.

Original class/function bodies supply every fixture, transform, collision and
sampling rule. Explicit namespace loading omits unrelated registry __init__s.
Audited AST extraction omits unused native imports; no dependency is mocked.
Original files are never edited. Unsupported environment retries fail loudly.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.machinery
import json
import os
import resource
import signal
import sys
import time
import types
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPOSITORY_ROOT / ".robot-worlds/existing-scenes-2026-11-02/robocasa-kitchen"
CASA = ROOT / "source/robocasa/robocasa"
SUITE = ROOT / "assembly-audit/robosuite-source/robosuite"
OUTPUT = None
AUDIT = []
SOURCE_PINS = {
    "robocasa": "3443cdf3dd20f9ae8ed41adba442059b506891d0",
    "robosuite": "5ce6643f3092639d08f7b0f90ed1c6a84f50552c",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def configure():
    global ROOT, CASA, SUITE, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Preserved RoboCasa source snapshot; defaults beneath this checkout's .robot-worlds.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New output directory; existing directories are rejected to preserve evidence.",
    )
    parser.add_argument(
        "--repair-environment-only",
        action="store_true",
        help="Update the isolated assembly environment's read-only dependency path, then exit.",
    )
    parser.add_argument(
        "--dependency-site",
        type=Path,
        help="Explicit existing NumPy/Pillow site-packages path for environment relocation.",
    )
    args = parser.parse_args()
    ROOT = args.source_root.resolve()
    CASA = ROOT / "source/robocasa/robocasa"
    SUITE = ROOT / "assembly-audit/robosuite-source/robosuite"
    if args.repair_environment_only:
        if args.dependency_site is None:
            parser.error("--repair-environment-only requires --dependency-site")
        dependency_site = args.dependency_site.resolve()
        if not all((dependency_site / package).is_dir() for package in ["numpy", "PIL"]):
            parser.error("--dependency-site must contain the existing numpy and PIL packages")
        bindings = list(
            (ROOT / "assembly-env/lib").glob(
                "python*/site-packages/readonly_workspace_dependencies.pth"
            )
        )
        if len(bindings) != 1:
            parser.error("Expected exactly one preserved isolated assembly environment binding")
        binding = bindings[0]
        old_value = binding.read_text()
        binding.write_text(str(dependency_site) + "\n")
        record = dict(
            binding=str(binding),
            previous_value=old_value.strip(),
            current_value=str(dependency_site),
            source_assets_modified=False,
            packages_installed=False,
            note="Only the active .pth binding changed; historical environment reports and venv creation metadata remain unchanged.",
        )
        receipt = ROOT / "assembly-environment-relocation.json"
        if receipt.exists():
            receipt = ROOT / f"assembly-environment-relocation-{time.time_ns()}.json"
        receipt.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record, indent=2))
        return False
    if args.output is None:
        parser.error("Assembly requires --output pointing to a new directory")
    OUTPUT = args.output.resolve()
    if OUTPUT.exists():
        parser.error("--output already exists; choose a new directory to preserve evidence")
    for original in [
        ROOT / "source",
        ROOT / "assembly-audit",
        ROOT / "assembly-env",
        ROOT / "assembly-config",
    ]:
        if OUTPUT == original or original in OUTPUT.parents:
            parser.error(
                "--output must be separate from original sources, configuration and environment"
            )
    return True


def verify_pinned_sources():
    verified = []
    for name, base, metadata, tree_path in [
        (
            "robocasa",
            ROOT / "source/robocasa",
            ROOT / "github-commit.json",
            ROOT / "github-tree.json",
        ),
        (
            "robosuite",
            ROOT / "assembly-audit/robosuite-source",
            ROOT / "assembly-audit/robosuite-commit.json",
            ROOT / "assembly-audit/robosuite-tree.json",
        ),
    ]:
        commit = json.loads(metadata.read_text())
        tree = json.loads(tree_path.read_text())
        # The preserved recursive-tree responses are addressed by the commit
        # SHA and echo that requested SHA; a tree-addressed response may instead
        # expose the commit's underlying tree SHA. Both must match this pin.
        allowed_tree_ids = {SOURCE_PINS[name], commit["commit"]["tree"]["sha"]}
        if (
            commit["sha"] != SOURCE_PINS[name]
            or tree["sha"] not in allowed_tree_ids
            or tree.get("truncated")
        ):
            raise RuntimeError("Original source pin/tree mismatch: " + name)
        entries = {entry["path"]: entry for entry in tree["tree"] if entry["type"] == "blob"}
        for path in base.rglob("*.py"):
            relative = path.relative_to(base).as_posix()
            entry = entries.get(relative)
            if entry is None:
                raise RuntimeError(
                    "Unpinned Python module inside original source tree: " + str(path)
                )
            data = path.read_bytes()
            git_blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if git_blob != entry["sha"]:
                raise RuntimeError("Original Python source modified: " + str(path))
            verified.append(dict(project=name, path=relative, source_sha256=sha(data)))
    AUDIT.append(
        dict(
            kind="pinned original Python sources verified",
            pins=SOURCE_PINS,
            files=verified,
            note="Every cached original Python module matches its pinned Git blob before execution.",
        )
    )


def namespace(name, paths):
    module = types.ModuleType(name)
    module.__path__ = [str(p) for p in paths]
    module.__package__ = name
    module.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=True)
    module.__spec__.submodule_search_locations = module.__path__
    bind(name, module)
    AUDIT.append(
        dict(
            kind="explicit namespace package",
            name=name,
            paths=module.__path__,
            note="No functional API replacement; original submodules/classes are loaded below. Registry initialization intentionally not executed.",
        )
    )
    return module


def bind(name, module):
    sys.modules[name] = module
    parent, _, leaf = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], leaf, module)


def original_nodes(path, names=None, classes=False):
    source = path.read_text()
    tree = ast.parse(source)
    kind = (
        (ast.ClassDef, ast.FunctionDef)
        if classes == "all"
        else ast.ClassDef
        if classes
        else ast.FunctionDef
    )
    nodes = [
        node
        for node in tree.body
        if isinstance(node, kind) and (names is None or node.name in names)
    ]
    if names is not None and {node.name for node in nodes} != set(names):
        raise RuntimeError("original source definitions not found: " + str(names))
    return source, nodes


def source_module(name, path, names, globals_, *, classes=False):
    source, nodes = original_nodes(path, names, classes=classes)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]
    module.__dict__.update(globals_)
    bind(name, module)
    tree = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)
    AUDIT.append(
        dict(
            kind="original AST definitions",
            module=name,
            source=str(path),
            source_sha256=sha(source.encode()),
            definitions=[node.name for node in nodes],
            ast_sha256=sha(ast.dump(tree, include_attributes=False).encode()),
            omitted="Module imports/other top-level code only; function bodies unchanged. Unavailable optional globals are unbound, never mocked.",
        )
    )
    return module


def class_method(path, class_name, method_name, globals_):
    source = path.read_text()
    cls = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    node = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    tree = ast.Module(body=[node], type_ignores=[])
    env = dict(globals_)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), env)
    AUDIT.append(
        dict(
            kind="original method",
            source=str(path),
            source_sha256=sha(source.encode()),
            class_name=class_name,
            method_name=method_name,
            lines=[node.lineno, node.end_lineno],
            ast_sha256=sha(ast.dump(node, include_attributes=False).encode()),
        )
    )
    return env[method_name]


def assert_no_native_or_robot_imports():
    forbidden = [
        name
        for name in sys.modules
        if name in {"mujoco", "numba", "scipy"}
        or name.startswith(("robosuite.robots", "robosuite.controllers", "robosuite.environments"))
    ]
    if forbidden:
        raise RuntimeError("unexpected native/environment import boundary: " + repr(forbidden))


def main():
    verify_pinned_sources()
    import numpy as np
    import PIL
    import yaml

    started = time.monotonic()
    signal.signal(
        signal.SIGALRM,
        lambda *_: (_ for _ in ()).throw(TimeoutError("source-only assembly30-second limit")),
    )
    signal.alarm(30)
    before = json.loads((ROOT / "acquired-source-files.json").read_text())
    for row in before:
        data = (CASA / "models/assets" / row["path"]).read_bytes()
        if sha(data) != row["sha256"]:
            raise RuntimeError("original asset integrity mismatch: " + row["path"])
    namespace("robosuite", [ROOT / "assembly-config/robosuite", SUITE])
    namespace("robocasa", [ROOT / "assembly-config/robocasa", CASA])
    # isort: off
    # Imports below are staged around explicit namespace initialization.
    importlib.import_module("robosuite.utils")
    import robosuite.macros as suite_macros

    if suite_macros.ENABLE_NUMBA:
        raise RuntimeError("official Numba macro must be false")
    source_module(
        "robosuite.utils.numba",
        SUITE / "utils/numba.py",
        ["jit_decorator"],
        dict(macros=suite_macros),
    )
    import robosuite.utils.transform_utils as T

    importlib.import_module("robosuite.models")
    suite_objects = namespace("robosuite.models.objects", [SUITE / "models/objects"])
    object_base = importlib.import_module("robosuite.models.objects.objects")
    for name in ["MujocoObject", "MujocoXMLObject", "MujocoGeneratedObject"]:
        setattr(suite_objects, name, getattr(object_base, name))
    generated = importlib.import_module("robosuite.models.objects.generated_objects")
    for name in ["CompositeBodyObject", "CompositeObject", "PrimitiveObject"]:
        setattr(suite_objects, name, getattr(generated, name))
    primitive = importlib.import_module("robosuite.models.objects.primitive")
    for name in ["BallObject", "BoxObject", "CapsuleObject", "CylinderObject"]:
        setattr(suite_objects, name, getattr(primitive, name))
    AUDIT.append(
        dict(
            kind="real original class reexports",
            module="robosuite.models.objects",
            sources=[object_base.__file__, generated.__file__, primitive.__file__],
            note="Only actual source classes reexported; unrelated group-object registry would import sim_utils/native environment helpers and is not executed.",
        )
    )
    import robosuite.utils.mjcf_utils as MJCF

    namespace("robosuite.models.arenas", [SUITE / "models/arenas"])
    arena_module = importlib.import_module("robosuite.models.arenas.arena")
    sys.modules["robosuite.models.arenas"].Arena = arena_module.Arena
    importlib.import_module("robocasa.models")
    import robocasa.macros as macros

    importlib.import_module("robocasa.utils")
    import robocasa.models.objects.objects as casa_objects

    OU = source_module(
        "robocasa.utils.object_utils",
        CASA / "utils/object_utils.py",
        None,
        dict(
            np=np,
            T=T,
            MJCFObject=casa_objects.MJCFObject,
            array_to_string=MJCF.array_to_string,
            string_to_array=MJCF.string_to_array,
            xml_path_completion=MJCF.xml_path_completion,
        ),
    )
    namespace("robocasa.models.scenes", [CASA / "models/scenes"])
    fixtures = namespace("robocasa.models.fixtures", [CASA / "models/fixtures"])
    fixture_base = importlib.import_module("robocasa.models.fixtures.fixture")
    fixtures.Fixture = fixture_base.Fixture
    fixtures.FixtureType = fixture_base.FixtureType
    import re

    source_module(
        "robocasa.models.fixtures.sink",
        CASA / "models/fixtures/sink.py",
        ["Sink"],
        dict(
            deepcopy=deepcopy,
            np=np,
            re=re,
            Fixture=fixture_base.Fixture,
            MJCFObject=casa_objects.MJCFObject,
            convert_quat=T.convert_quat,
            T=T,
            array_to_string=MJCF.array_to_string,
            string_to_array=MJCF.string_to_array,
            xml_path_completion=MJCF.xml_path_completion,
        ),
        classes=True,
    )
    stove_path = CASA / "models/fixtures/stove.py"
    stove_tree = ast.parse(stove_path.read_text())
    stove_locations = next(
        ast.literal_eval(n.value)
        for n in stove_tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "STOVE_LOCATIONS" for t in n.targets)
    )
    source_module(
        "robocasa.models.fixtures.stove",
        stove_path,
        ["Stove", "Stovetop"],
        dict(
            deepcopy=deepcopy,
            np=np,
            Fixture=fixture_base.Fixture,
            T=T,
            OU=OU,
            array_to_string=MJCF.array_to_string,
            string_to_array=MJCF.string_to_array,
            find_elements=MJCF.find_elements,
            STOVE_LOCATIONS=stove_locations,
        ),
        classes=True,
    )
    accessories = importlib.import_module("robocasa.models.fixtures.accessories")
    source_module(
        "robocasa.models.fixtures.windows",
        CASA / "models/fixtures/windows.py",
        None,
        dict(
            os=os,
            np=np,
            BoxObject=suite_objects.BoxObject,
            CompositeBodyObject=suite_objects.CompositeBodyObject,
            CustomMaterial=MJCF.CustomMaterial,
            a2s=MJCF.array_to_string,
            s2a=MJCF.string_to_array,
            xml_path_completion=MJCF.xml_path_completion,
            robocasa=sys.modules["robocasa"],
            Fixture=fixture_base.Fixture,
            WallAccessory=accessories.WallAccessory,
            MujocoXMLObjectRobocasa=casa_objects.MujocoXMLObjectRobocasa,
            T=T,
            get_pos_after_rel_offset=OU.get_pos_after_rel_offset,
        ),
        classes="all",
    )
    fixtures.__file__ = str(CASA / "models/fixtures/__init__.py")
    exec(compile(Path(fixtures.__file__).read_text(), fixtures.__file__, "exec"), fixtures.__dict__)
    import robocasa.models.fixtures.fixture_utils as FixtureUtils
    import robocasa.utils.placement_samplers as samplers
    from robocasa.utils.errors import PlacementError, SamplingError
    from robocasa.models.scenes.kitchen_arena import KitchenArena
    from robosuite.models.world import MujocoWorldBase
    # isort: on

    env_globals = dict(
        np=np,
        T=T,
        ET=ET,
        deepcopy=deepcopy,
        macros=macros,
        OU=OU,
        PlacementError=PlacementError,
        SamplingError=SamplingError,
        SequentialCompositeSampler=samplers.SequentialCompositeSampler,
        UniformRandomSampler=samplers.UniformRandomSampler,
        array_to_string=MJCF.array_to_string,
    )
    EnvUtils = source_module(
        "robocasa.utils.env_utils",
        CASA / "utils/env_utils.py",
        [
            "get_single_fixture_sampler",
            "_get_placement_initializer",
            "_check_cfg_is_valid",
            "find_object_cfg_by_name",
        ],
        env_globals,
    )
    kitchen_path = CASA / "environments/kitchen/kitchen.py"
    kitchen_globals = dict(
        env_globals,
        EnvUtils=EnvUtils,
        Fixture=fixtures.Fixture,
        FixtureType=fixtures.FixtureType,
        fixture_is_type=fixtures.fixture_is_type,
        FixtureUtils=FixtureUtils,
        defaultdict=defaultdict,
    )
    get_fixture = class_method(kitchen_path, "Kitchen", "get_fixture", kitchen_globals)

    class SourceFixtureStageContext:
        """State carrier for real fixtures and original methods; never a simulator."""

        def _destroy_sim(self):
            raise RuntimeError(
                "Original fixture sampler exhausted retries; full-environment restart is outside this source-only stage. No placement repair attempted."
            )

        def _load_model(self, *args, **kwargs):
            raise RuntimeError(
                "Original full-environment recursion requested; source-only stage stops."
            )

    SourceFixtureStageContext.get_fixture = get_fixture
    context = SourceFixtureStageContext()
    context.rng = np.random.default_rng(0)
    # Original counters randomize invisible region annotation RGB via legacy
    # np.random. Seed it explicitly for reproducible source annotation bytes.
    np.random.seed(0)
    context._ep_meta = {}
    context.layout_and_style_ids = [[4, 4]]
    setup_source = kitchen_path.read_text()
    kitchen_class = next(
        n
        for n in ast.parse(setup_source).body
        if isinstance(n, ast.ClassDef) and n.name == "Kitchen"
    )
    setup = next(
        n for n in kitchen_class.body if isinstance(n, ast.FunctionDef) and n.name == "_setup_model"
    )
    choose = next(
        n for n in setup.body if isinstance(n, ast.If) and "layout_and_style_ids" in ast.dump(n)
    )
    exec(
        compile(ast.Module(body=[choose], type_ignores=[]), str(kitchen_path), "exec"),
        dict(kitchen_globals, self=context),
    )
    AUDIT.append(
        dict(
            kind="original first-episode scene-selection statement",
            source=str(kitchen_path),
            lines=[choose.lineno, choose.end_lineno],
            ast_sha256=sha(ast.dump(choose, include_attributes=False).encode()),
            seed=0,
            legacy_numpy_seed=0,
            scene_candidates=context.layout_and_style_ids,
        )
    )
    context.mujoco_arena = KitchenArena(
        layout_id=context.layout_id, style_id=context.style_id, rng=context.rng, clutter_mode=1
    )
    context.mujoco_arena.set_origin([0, 0, 0])
    context.fixture_cfgs = context.mujoco_arena.get_fixture_cfgs()
    context.fixtures = {cfg["name"]: cfg["model"] for cfg in context.fixture_cfgs}
    context.objects = {}
    context.object_cfgs = []
    context.model = MujocoWorldBase(enable_multiccd=True, enable_sleeping_islands=False)
    task_path = SUITE / "models/tasks/task.py"
    merge_globals = dict(MujocoObject=suite_objects.MujocoObject)
    class_method(task_path, "Task", "merge_arena", merge_globals)(
        context.model, context.mujoco_arena
    )
    class_method(task_path, "Task", "merge_objects", merge_globals)(
        context.model, list(context.fixtures.values())
    )

    original = next(
        n for n in kitchen_class.body if isinstance(n, ast.FunctionDef) and n.name == "_load_model"
    )
    statements = [n for n in original.body if 663 <= n.lineno and n.end_lineno <= 794]
    if len(statements) != 12 or statements[0].lineno != 663 or statements[-1].end_lineno != 794:
        raise RuntimeError("Pinned original placement-stage boundaries drifted")
    stage = deepcopy(original)
    stage.name = "_source_fixture_placement_stage"
    stage.body = deepcopy(statements)
    stage.decorator_list = []
    function_module = ast.Module(body=[stage], type_ignores=[])
    stage_globals = dict(kitchen_globals)
    exec(
        compile(ast.fix_missing_locations(function_module), str(kitchen_path), "exec"),
        stage_globals,
    )
    AUDIT.append(
        dict(
            kind="original fixture stage, verbatim AST statements",
            source=str(kitchen_path),
            source_sha256=sha(setup_source.encode()),
            method="Kitchen._load_model",
            lines=[663, 794],
            statement_count=len(statements),
            ast_sha256=sha(
                ast.dump(
                    ast.Module(body=statements, type_ignores=[]), include_attributes=False
                ).encode()
            ),
            excluded="Robot/full-environment setup before this stage, task objects and robot references after it; no statement within the fixture stage changed.",
        )
    )
    calls = []

    def profile(frame, event, arg):
        if (
            event == "call"
            and frame.f_code.co_name == "get_single_fixture_sampler"
            and frame.f_code.co_filename == str(CASA / "utils/env_utils.py")
        ):
            cfg = frame.f_locals["cfg"]
            calls.append([x["name"] for x in cfg] if isinstance(cfg, list) else [cfg["name"]])

    assert_no_native_or_robot_imports()
    sys.setprofile(profile)
    try:
        stage_globals[stage.name](context)
    finally:
        sys.setprofile(None)
    assert_no_native_or_robot_imports()

    xml = context.model.get_xml()
    parsed = ET.fromstring(xml)
    missing = [
        dict(tag=e.tag, file=e.get("file"))
        for e in parsed.iter()
        if e.get("file") and not Path(e.get("file")).is_file()
    ]
    if missing:
        raise RuntimeError(
            "Original constructors left unresolved asset references: " + repr(missing)
        )
    for row in before:
        if sha((CASA / "models/assets" / row["path"]).read_bytes()) != row["sha256"]:
            raise RuntimeError("Original source asset modified")
    fixture_info = []
    for name, fixture in context.fixtures.items():
        rotation = getattr(fixture, "rot", None)
        fixture_info.append(
            dict(
                name=name,
                class_name=type(fixture).__name__,
                position=np.asarray(fixture.pos).tolist(),
                rotation=None if rotation is None else float(rotation),
                size=None if fixture.size is None else np.asarray(fixture.size).tolist(),
                source_body_attributes=dict(fixture.get_obj().attrib),
            )
        )
    placements = [
        dict(
            name=name,
            position=np.asarray(value[0]).tolist(),
            quaternion_wxyz=np.asarray(value[1]).tolist(),
            fixture_name=value[2].name,
        )
        for name, value in context.fxtr_placements.items()
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    xml_path = OUTPUT / "kitchen_layout004_style004_clutter1_seed0.xml"
    xml_path.write_text(xml)
    (OUTPUT / "source-execution-audit.json").write_text(json.dumps(AUDIT, indent=2) + "\n")
    (OUTPUT / "source-fixtures.json").write_text(json.dumps(fixture_info, indent=2) + "\n")
    (OUTPUT / "source-fixture-placements.json").write_text(
        json.dumps(dict(sampler_call_sequence=calls, placements=placements), indent=2) + "\n"
    )
    report = dict(
        xml=str(xml_path),
        xml_bytes=len(xml.encode()),
        xml_sha256=sha(xml.encode()),
        source_root=str(ROOT),
        source_pins=SOURCE_PINS,
        assembly_tool_sha256=sha(Path(__file__).read_bytes()),
        fixtures=len(context.fixtures),
        sampled_fixture_placements=len(placements),
        sampler_calls=len(calls),
        bodies=len(parsed.findall(".//body")),
        joints=len(parsed.findall(".//joint")),
        geoms=len(parsed.findall(".//geom")),
        asset_file_references=len([e for e in parsed.iter() if e.get("file")]),
        missing_asset_references=missing,
        source_assets_unchanged=len(before),
        elapsed_seconds=time.monotonic() - started,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        numpy_version=np.__version__,
        yaml_version=yaml.__version__,
        yaml_native_extension_enabled=yaml.__with_libyaml__,
        pillow_version=PIL.__version__,
        mujoco_imported="mujoco" in sys.modules,
        numba_imported="numba" in sys.modules,
        scipy_imported="scipy" in sys.modules,
        native_compilation_performed=False,
        rendering_performed=False,
        physics_verified=False,
        task_objects_added=False,
        mobility_changed=False,
        notes=[
            "Only original source fixture/XML/sampling algorithms executed. Explicit audited namespace/AST source slicing avoids registry/robot/native imports.",
            "Legacy NumPy RNG seeded0 for original invisible region annotation RGB; source Generator RNG independently seeded0.",
            "Source failed full-environment retry boundaries raise; no sampler/geometry repair fallback.",
            "Robot-dependent camera initialization, task objects and full environment initialization intentionally outside this fixture-only export.",
        ],
    )
    (OUTPUT / "assembly-report.json").write_text(json.dumps(report, indent=2) + "\n")
    signal.alarm(0)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if configure():
        try:
            main()
        except Exception as error:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            (OUTPUT / "assembly-failure.json").write_text(
                json.dumps(
                    dict(
                        error_type=type(error).__name__,
                        error=str(error),
                        source_execution_audit=AUDIT,
                        native_compilation_performed=False,
                    ),
                    indent=2,
                )
                + "\n"
            )
            raise
