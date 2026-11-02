"""Astra high scene decisions, 2026-11-02; data authoring and static checks only.

Preserves released presets. Reuses the checked v1 factory task definitions and
original prepared assets, recomposes their whole station groups, then restores
the architectural density of the earlier warehouse reference. No model API,
MuJoCo compilation, renderer or robot policy is called by this authoring script.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from robot_worlds.asset_library import AssetLibrary
from robot_worlds.generator import _source_only_layout, validate_spec
from robot_worlds.large_scene import expand_assemblies
from robot_worlds.prepared_assets import GenerationSpec, materialize_prepared_assets
from robot_worlds.task_catalog import TaskCatalog

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.robot-worlds/high-fidelity-presets-2026-11-02/astra-authored/factory'
PREVIOUS = ROOT / 'src/robot_worlds/assets/starters/simready-factory'
REFERENCE = ROOT / '.robot-worlds/live-warehouse-presentation-03/generation-plan-2.json'
AUTHOR = {'model': 'gpt-6-astra', 'reasoning': 'high',
          'method': 'direct Codex subagent authoring; no paid model CLI/API call',
          'date': '2026-11-02'}


def read(path):
    return json.loads(path.read_text())


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def make_library():
    destination = OUT / 'asset-library'
    if destination.exists():
        return AssetLibrary.open(destination)
    result = AssetLibrary()
    selections = {
        'asset-library': ['rack_large_empty_a02', 'pallet_a01',
                          'box_corrugated_brown_b20', 'barrier_fence_metal_a02'],
        'workbench-library': ['table_packing_heavyduty_c01'],
        'living-room-library-v2': ['fixture_cabinet_b01', 'misc_notebook_a01',
                                  'handTool_screwdriver_phillips_ph2_a01'],
    }
    for folder, identifiers in selections.items():
        library = AssetLibrary.open(ROOT / '.robot-worlds' / folder)
        for identifier in identifiers:
            result.add(identifier, library.load(identifier, prefix='asset'),
                       description=library.description(identifier))
    result.save(destination)
    return AssetLibrary.open(destination)


def author_factory():
    OUT.mkdir(parents=True, exist_ok=True)
    library = make_library()
    previous = read(PREVIOUS / 'generation-plan.json')
    tasks = read(PREVIOUS / 'task-suite.json')
    reference = read(REFERENCE)
    plan = copy.deepcopy(previous)
    plan.update(name='Stocked factory with connected manual workcells',
                description='Six fully stocked source rack banks, receiving pallets, four '
                'manual stations and real tool drawers in a framed industrial hall.',
                bounds=[20, 16, 5], robot_start=[3.0, -6.3, 1.5707963267948966])
    # Architecture keeps the source warehouse's measured, butt-jointed shell.
    # No furniture or prop is synthesized, rescaled or recolored here.
    plan['objects'] = [copy.deepcopy(o) for o in reference['objects']
                       if o['kind'] == 'box']
    plan['assembly_templates'] = copy.deepcopy(reference['assembly_templates'])
    for template in plan['assembly_templates']:
        for part in template['parts']:
            category = 'Wall-frame architecture: ' if template['id'] in ['wall', 'bay', 'roof'] else 'Floor paint: '
            part['label'] = category + part['label']
    plan['assembly_instances'] = [copy.deepcopy(a) for a in reference['assembly_instances']
                                  if a['id'] in ['north', 'west', 'east', 'bays', 'overhead']]
    # Whole station groups preserve original support-relative offsets, yaw,
    # source scales, source joints and task-addressed IDs.
    stations = {
        'receiving': {'position': [5.5, -4.7, 0],
                      'assets': ['receiving', 'receiving_carton'],
                      'regions': ['carton_staging']},
        'preparation': {'position': [5.5, -1.3, 0],
                        'assets': ['preparation', 'primary_tool', 'spare_tool', 'work_order'],
                        'regions': []},
        'inspection': {'position': [5.5, 2.1, 0], 'assets': ['inspection'],
                       'regions': ['inspection_loading']},
        'dispatch': {'position': [5.5, 5.5, 0],
                     'assets': ['dispatch', 'outbound_tool'], 'regions': ['outbound_kit']},
        'tool_cabinet': {'position': [8.8, -.8, 0],
                         'assets': ['tool_cabinet', 'stored_tool'],
                         'regions': ['tool_lower_closed']},
    }
    originals = {a['id']: a for a in previous['prepared_assets']}
    region_originals = {r['id']: r for r in previous['regions']}
    plan['prepared_assets'], plan['regions'] = [], []
    for identifier, station in stations.items():
        delta = [new - old for new, old in zip(station['position'], originals[identifier]['position'])]
        for collection, names, source in [('prepared_assets', station['assets'], originals),
                                          ('regions', station['regions'], region_originals)]:
            for name in names:
                entry = copy.deepcopy(source[name])
                entry['position'] = [round(x + d, 12) for x, d in zip(entry['position'], delta)]
                plan[collection].append(entry)

    def asset(identifier, kind, position, quarter_turns=0, **options):
        plan['prepared_assets'].append(dict(id=identifier, asset_id=kind,
                                            position=position, quarter_turns=quarter_turns, **options))

    # Source shelf deck tops inspected in the previous dense warehouse plan.
    # Two rows remain inside the actual 1.7m-deep source shelf clearance.
    deck_tops = [.5059940671740328, 1.630106580460421, 2.3814070596938556]
    for column, x in enumerate([-6.5, -.5]):
        for row, y in enumerate([-3., 1., 5.]):
            rack_id = f'stock_{column}_{row}'
            asset(rack_id, 'rack_large_empty_a02', [x, y, .001])
            for level, z in enumerate(deck_tops):
                asset(f'{rack_id}_carton_{level}', 'box_corrugated_brown_b20',
                      [x - 1.5, y - .48, z + .003],
                      repeat=[6, 2, 1], spacing=[.6, .96, 0])
    pallet_positions = [(-8., -6.), (-6., -6.), (-4., -6.), (8.5, 4.3), (8.5, 6.)]
    for number, (x, y) in enumerate(pallet_positions):
        asset(f'staged_pallet_{number}', 'pallet_a01', [x, y, .001])
        for slot, dx in enumerate([-.25, .25]):
            asset(f'pallet_load_{number}_{slot}', 'box_corrugated_brown_b20',
                  [x + dx, y, .21159552342909082])
    # Short end guards leave all storage cross-aisles and front task approaches open.
    for number, y in enumerate([-3., 1., 5.]):
        asset(f'rack_end_guard_{number}', 'barrier_fence_metal_a02', [1.82, y, .001], 1)

    def paint(identifier, x, y, width, depth, color=(1., .76, .02)):
        plan['objects'].append(dict(id=identifier, label='Floor paint: ' + identifier.replace('_', ' '),
                                   kind='box', position=[x, y, .006],
                                   size=[width, depth, .002], color=list(color), collision=False))

    # Real routes: south apron -> central aisle -> each table's south approach;
    # east return aisle connects outbound staging to the south shipping opening.
    paint('central_route_west', 2.25, 1.1, .055, 12.9)
    paint('central_route_east', 3.75, 1.1, .055, 12.9)
    paint('south_apron_north', -2.5, -4.65, 13.5, .055)
    paint('south_apron_south', -2.5, -5.25, 13.5, .055)
    paint('east_return_lane', 7.15, .45, .055, 12.5)
    colors = {'receiving': (.93, .61, .20), 'preparation': (.25, .58, .82),
              'inspection': (.22, .75, .57), 'dispatch': (.96, .80, .22)}
    for identifier, color in colors.items():
        x, y, _ = stations[identifier]['position']
        # Thin architectural paint, not hidden barriers or substitute furniture.
        for side, px, py, width, depth in [
            ('north', x, y + .78, 2.3, .04), ('south', x, y - .78, 2.3, .04),
            ('west', x - 1.15, y, .04, 1.56), ('east', x + 1.15, y, .04, 1.56),
        ]:
            paint(f'{identifier}_{side}_outline', px, py, width, depth, color)
    for number, y in enumerate([-4.7, -1.3, 2.1, 5.5]):
        plan['assembly_instances'].append(dict(id=f'flow_arrow_{number}', template_id='arrow',
                                              position=[3.0, y, 0], quarter_turns=0))
    plan['source_prompt'] = (
        'Astra high: compose a dense 20x16m manual factory around six fully stocked original '
        'NVIDIA SimReady rack banks and four connected receiving/preparation/inspection/dispatch '
        'worktables. Retain original source scale/materials, all real drawer articulation and '
        'physical props. Match the framed industrial warehouse reference. No conveyor, invented '
        'machining, powered transport or procedural substitute props. Preserve 15 task variants.'
    )
    plan['assumptions'] = [
        'Every scene, asset-selection, material and placement decision in v2 was authored '
        'directly by gpt-6-astra with high reasoning on 2026-11-02.',
        'Original prepared meshes, PBR materials, scale, masses, passive joints and collision '
        'adaptation records are retained. Architecture and paint alone use direct primitives.',
        'All 15 task definitions retain their movable-root and real support/joint identities '
        'from the earlier Astra-authored suite. Support-relative object offsets remain identical.',
        'There are 216 loose source cartons on six three-level racks, ten cartons on five '
        'source pallets and one receiving worktable carton. They retain source dynamics; this '
        'does not mean every stock item is reachable or liftable by the selected robot.',
        'Each source pallet weighs 50.73kg and is never a lifting target. The 0.299x0.622m '
        'receiving carton is pushed across the table; no one-handed lifting task is claimed.',
        'Inspection is an explicit three-second bookkeeping process gated by real supported, '
        'released placement. It is not optical metrology or material transformation.',
        'The source wood cabinet supplies two real passive drawers facing south; screwdriver '
        'storage uses its genuine cavity. No industrial material substitution is applied.',
        'Static schema, reference and geometry checks do not establish native settling, '
        'visual quality, reachability, task completion or viewport performance.',
    ]
    return plan, tasks, library, stations


def main():
    plan, tasks, library, stations = author_factory()
    parsed = GenerationSpec.model_validate(plan)
    _source_only_layout(parsed)
    world = expand_assemblies(materialize_prepared_assets(parsed, max_objects=5000,
                                                         asset_library=library), max_objects=5000)
    catalog = TaskCatalog.model_validate(tasks)
    catalog.validate_for(world)
    errors = validate_spec(world, validation_profile='strict')
    save('generation-plan.json', parsed.model_dump(mode='json'))
    save('task-suite.json', catalog.model_dump(mode='json'))
    save('static-validation.json', {
        'author': AUTHOR, 'checks': ['GenerationSpec', 'source-only layout',
          'prepared source materialization', 'assembly expansion', 'TaskCatalog references',
          'validate_spec strict'], 'objects': len(world.objects), 'tasks': len(catalog.tasks),
        'errors': errors, 'native_validation_run': False, 'visual_approval': False,
        'policy_runs': 0,
    })
    save('source-decision-manifest.json', {
        'author': AUTHOR, 'scene': 'factory', 'released_artifacts_modified': False,
        'reference_plan': str(REFERENCE.relative_to(ROOT)),
        'reference_sha256': hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
        'task_source': str((PREVIOUS / 'task-suite.json').relative_to(ROOT)),
        'task_source_sha256': hashlib.sha256((PREVIOUS / 'task-suite.json').read_bytes()).hexdigest(),
        'stations': stations, 'rack_centers': [[x, y, .001] for x in [-6.5, -.5]
                                             for y in [-3., 1., 5.]],
        'source_assets': list(library.asset_ids),
        'source_notices': 'Each selected library entry retains its upstream source/revision/license and conversion record.',
        'decisions': ['Original source appearance and scale throughout',
          'Six racks with 216 real movable source cartons',
          'Four real manual worktables with south-facing task positions',
          'Two south-facing real cabinet drawers and four independent screwdrivers',
          'Existing dark floor, pale wall panels, steel frame and yellow routes',
          'No passive conveyor presented as an automatic process',
          'No household or industrial props replaced by primitives'],
    })
    (OUT / 'semantic-plan.md').write_text(SEMANTIC_PLAN)
    print(json.dumps({'output': str(OUT), 'objects': len(world.objects),
                      'tasks': len(catalog.tasks), 'errors': errors}))
    if errors:
        raise SystemExit('Static checks require repair; native checks have not run.')


SEMANTIC_PLAN = '''# Astra high factory v2

A dense 20 × 16 × 5 m industrial hall retains the earlier warehouse's charcoal
floor, pale segmented wall panels, dark steel framing, paired loading openings
and open overhead beams. Original material maps and dimensions remain on every
source rack, carton, pallet, workbench, screwdriver and drawer cabinet. No prop
has been replaced with a primitive, recolored to disguise its origin, or scaled
to fit a convenient gap.

Six rack banks occupy x = −6.5 and −0.5 m, at y = −3, 1 and 5 m. Each is 4 m
wide and 2.067 m deep, leaving a 2 m longitudinal storage aisle and approximately
1.933 m between row ends. Each of three real deck levels supports two rows of
six original cartons, 216 independent physical cartons altogether. Carton rows
are at local y = ±0.48 m and remain inside the source shelf clearance. This is
stocked storage with actual loose contents, not a background image or rigidly
merged visual stock. High shelves are not claimed to be reachable by TIAGo++.

Four manual worktables sit at x = 5.5 m and y = −4.7, −1.3, 2.1, 5.5 m, in
receiving → preparation → inspection → dispatch order. Their 1.219 m depths
leave 2.181 m between stations. Tool positions and destinations are near each
south edge. The central clear route occupies approximately x = 2.3 to 4.3 m;
every front working position connects to it. The east return route runs near
x = 7.15 m, alongside the workcells. Five loaded source pallets serve west
receiving and northeast outbound staging. The short source rack end guards
protect the east rack ends without closing the cross-aisles.

The articulated source cabinet is at (8.8, −0.8), rotated 270 degrees so its two
drawers pull south. There is no table or pallet in its sweep or its south
approach. The stored screwdriver moves with the original drawer compartment;
its source body is not welded into the cabinet. Three more screwdrivers and a
work-order notebook occupy their original support-relative offsets on the
preparation and dispatch tables. The 50.73 kg pallets are not lift targets; the
receiving carton is a supported push task. The cabinet remains its original
wooden source asset; a material override would erase the source's appearance.

All fifteen prior task identities and executable criteria are preserved:
tool grasp/return, interstation carrying, work-order delivery, both drawer
opening/closing sequences, stored-tool retrieval, spare-tool storage, carton
staging, inspection-only and ordered inspection/dispatch. The inspection dwell
is explicit process bookkeeping gated by supported, released physical placement;
it does not simulate metrology, screw-driving or manufacturing transformation.
There is no conveyor and no implied automatic stock transport.

This is the authored candidate, not a newly released preset. Static schema and
reference checks are recorded separately. Native collision/settling checks,
drawer sweeps with contents, reachable approaches, grasp/transfer execution and
visual comparison with the warehouse reference remain engineering acceptance
work. No MuJoCo compile/render or robot policy trial was performed here.
'''

if __name__ == '__main__':
    main()
