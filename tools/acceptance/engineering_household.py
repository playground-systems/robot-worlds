"""Privileged engineering placement through public SDK actions; not an autonomous policy.

Uses original bowl/microwave geometry and source actuator limits. The door starts
open. Reads the simulator base transform to convert fixed fixture targets into
base-frame commands. Never changes object poses, contacts, masses or joint gains.
Separate sensor-only SDK/live checks establish the customer observation contract.
"""
import argparse
import json
import math
import time
import zipfile
from pathlib import Path

import numpy as np

from robot_worlds import World, asset_import, import_convex_mesh_asset, load, load_prepared_asset
from robot_worlds.models import SceneObject, WorldSpec
from robot_worlds.runtime import Runtime
from robot_worlds.sdk import Report
from robot_worlds.sensors import local_point


def engineering_scene():
    source = Path(asset_import.__file__).parent/'assets/household/porcelain_bowl'
    bowl = import_convex_mesh_asset(source/'meshes/model.obj', source/'collision/manifest.json',
        prefix='bowl', mass=.6, source_notice=source/'ATTRIBUTION.md',
        texture_path=source/'materials/textures/texture.png')
    minimum = min(o.position[2]-o.size[2]/2 for o in bowl.objects if o.collision)
    translation = np.array([1., 0., .6-minimum+.001])
    for obj in bowl.objects:
        obj.position = np.array(obj.position)+translation
        obj.position = (obj.position[0]-.1, obj.position[1], obj.position[2]+.08)
    oven = load_prepared_asset('nvidia-microwave079', prefix='oven', position=(1.30, -.04, .6543),
        quarter_turns=3, joint_initials=[{'name': 'microjoint', 'initial': -math.pi/2}]).asset
    objects = [
        SceneObject(id='source_counter', label='Engineering pickup counter', kind='box',
                    position=(1, 0, .67), size=(.28, .30, .02)),
        SceneObject(id='appliance_counter', label='Microwave counter', kind='box',
                    position=(1.30, -.04, .32715), size=(.32, .85, .6543)),
        *bowl.objects, *oven.objects,
    ]
    for i, y in enumerate((-.46, .46)):
        objects.append(SceneObject(id='counter_pier_'+str(i), label='Engineering counter support', kind='box',
                                   position=(1, y, .31), size=(.28, .08, .62)))
    objects.append(SceneObject(id='counter_crossbeam', label='Pickup counter support beam below door sweep',
                               kind='box', position=(1, 0, .64), size=(.28, 1, .04)))
    subject = bowl.body_ids['object']
    return WorldSpec(name='Engineering fixture · original bowl and microwave',
        description='Fixed privileged engineering fixture. Original assets; initially open door. Not text-generated or autonomous.',
        task='Put the white porcelain bowl inside the microwave and leave it resting there.',
        bounds=(8, 6, 3), robot_start=(.2, 0, 0), objects=objects, regions=oven.regions,
        assumptions=[*bowl.disclosures, *oven.disclosures,
                     'Bowl mass is estimated as 0.6 kg; source geometry is unchanged.',
                     'Engineering controller knows fixture targets and reads the robot base transform. No object pose writes.'],
        rubric=[dict(id='inside', description='Entire bowl inside cavity', predicate='in_region', object_id=subject,
                     target_id=oven.regions[0].id, tolerance=.002, duration=.2),
                dict(id='supported', description='Bowl contacts tray', predicate='on_top', object_id=subject,
                     target_id=oven.geom_ids['tray'], tolerance=.005, duration=.2),
                dict(id='released', description='No gripper holding', predicate='released', object_id=subject,
                     intent='maintain', duration=.2)])


class EngineeringPlacement:
    provenance = {'evidence_class': 'privileged engineering fixture', 'autonomous': False,
                  'privileged_reads': ['robot base transform'], 'object_pose_writes': False,
                  'actions': 'public SDK controller actions only', 'initial_door': 'open',
                  'task': 'grasp, short carry, guided insertion, release and stable support'}

    def __init__(self, runtime_holder):
        self.runtime_holder = runtime_holder

    def reset(self, context):
        self.actions = self.sequence()

    def act(self, observation):
        self.observation = observation
        return next(self.actions, {'tool': 'finish', 'arguments': {}})

    def sequence(self):
        runtime = self.runtime_holder['runtime']
        def action(tool, **arguments):
            return {'tool': tool, 'arguments': arguments}
        def move(point, hand='right', approach='forward_down'):
            # Explicit engineering privilege. Production policies receive no
            # global transform; their coordinates come from onboard perception.
            return action('move_arm', hand=hand, position=local_point(point, *runtime._base_transform()), approach=approach)
        def grip(hand, opening):
            return action('gripper', hand=hand, opening=opening)
        home = [.3, -.8, 0, 1.5, 0, 0, 0]
        yield action('move_torso', extension=.03)
        yield grip('left', .045)
        yield move([.9, .094, .88], 'left')
        yield grip('right', .045)
        yield move([.9, -.094, .88])
        yield move([.9, .094, .735], 'left')
        yield grip('left', 0)
        yield move([.9, -.094, .735])
        yield grip('right', 0)
        yield action('move_torso', extension=.15)
        yield action('wait', seconds=1)
        yield action('drive', distance=.07, turn=0)
        yield action('move_torso', extension=.055)
        yield grip('left', .045)
        yield move([.98, .094, .88], 'left')
        yield action('move_arm_joints', hand='left', positions=home)
        yield action('drive', distance=.36, turn=0)
        yield action('drive', distance=.075, turn=0)
        yield grip('left', .045)
        yield grip('right', .045)
        yield action('move_torso', extension=.115)
        yield action('move_arm_joints', hand='right', positions=home)
        yield action('drive', distance=-.30, turn=0)
        yield action('drive', distance=0, turn=math.pi/2-self.observation['robot']['relative_heading_rad'])
        yield action('drive', distance=.12, turn=0)
        yield action('drive', distance=0, turn=-self.observation['robot']['relative_heading_rad'])
        yield action('drive', distance=0, turn=-self.observation['robot']['relative_heading_rad'])
        yield action('move_torso', extension=.03)
        yield grip('right', .045)
        yield move([1.05, .03, .85], approach='forward')
        for x in (1.05, 1.10, 1.15, 1.20):
            yield move([x, .03, .78], approach='forward')
        yield move([1.05, .03, .85], approach='forward')
        yield action('wait', seconds=1)
        yield action('finish')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--render', action='store_true', help='Record camera and bounded replay frames')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    world = World.from_spec(engineering_scene(), args.output/'worlds')
    validation_seconds = time.monotonic()-start
    record = {'evidence_class': EngineeringPlacement.provenance, 'world_id': world.id,
              'validation': world.manifest['validation'], 'paid_model_calls': 0}
    (args.output/'acceptance.json').write_text(json.dumps(record, indent=2)+'\n')
    print('validation', world.manifest['validation']['status'], flush=True)
    assert world.manifest['validation']['status'] == 'ready', [
        {'check': c['check'], 'errors': c.get('errors', [])}
        for c in world.manifest['validation']['checks'] if not c['passed']]
    if args.validate_only:
        return
    holder = {}
    def runtime_factory(*params, **options):
        holder['runtime'] = Runtime(*params, **options)
        return holder['runtime']
    inspection_views = []
    def event(value):
        if value.get('type') in {'step', 'episode_finished'}:
            print(value['type'], value.get('step', ''), flush=True)
        capture = None
        if args.render and value.get('type') == 'step' and value.get('step') == 9:
            capture = ('grasp', dict(lookat=[.8, 0, 1.0], azimuth=135, elevation=-25, distance=1.6))
        if (args.render and value.get('type') == 'action_started'
                and value.get('action', {}).get('tool') == 'finish'):
            capture = ('microwave-interior', dict(lookat=[1.18, 0, .75], azimuth=45, elevation=-10, distance=.45))
        if capture:
            name, camera = capture
            member = f'episode-000/inspection-{name}.png'
            path = args.output/'runs'/value['run_id']/member
            try:
                path.write_bytes(holder['runtime'].render(view='overview', **camera))
                inspection_views.append({'name': name, 'path': member,
                    'time': float(holder['runtime'].data.time), 'requested_camera': camera,
                    'note': 'Actual native state; camera clearance may adjust the requested eye.'})
            except Exception as error:
                inspection_views.append({'name': name, 'error': type(error).__name__})
    evaluation_start = time.monotonic()
    report = world.evaluate(EngineeringPlacement(holder), runtime_factory=runtime_factory,
        render=args.render, max_steps=40, max_sim_seconds=150, max_replay_frames=24,
        output=args.output/'runs', on_event=event)
    record.update(run_id=report.path.name, summary=report.summary,
                  validation_seconds=validation_seconds, evaluation_seconds=time.monotonic()-evaluation_start)
    portable_start = time.monotonic()
    for artifact, name in ((world, 'world'), (report, 'report')):
        archive = artifact.export(args.output/(name+'.zip'))
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(args.output/('restored-'+name))
    restored = load(args.output/'restored-world')
    saved = Report(args.output/'restored-report')
    assert restored.id == world.id and saved.summary == report.summary
    assert saved.regrade(restored, output=args.output/'regraded').summary == report.summary
    record['portable_reload_and_regrade'] = True
    record['portable_seconds'] = time.monotonic()-portable_start
    record['elapsed_seconds'] = time.monotonic()-start
    import resource
    import sys
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record['peak_process_memory_bytes'] = rss if sys.platform == 'darwin' else rss*1024
    record['rendered'] = args.render
    record['inspection_views'] = inspection_views
    (args.output/'acceptance.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps({'summary': report.summary, 'elapsed_seconds': record['elapsed_seconds']}), flush=True)
    assert report.summary['valid'] == 1 and report.summary['successes'] == 1, report.summary
    if args.render:
        assert len(inspection_views) == 2 and all('path' in v for v in inspection_views), inspection_views


if __name__ == '__main__':
    main()
