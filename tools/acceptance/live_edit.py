"""Bounded live scoped edit → sensor-only rollout → portable report acceptance.

Uses an existing Codex sign-in, without reading or exporting its credentials.
The edit scope is explicit; the remaining world and every requirement are checked.
"""
import argparse
import json
import time
import zipfile
from pathlib import Path

from robot_worlds import load
from robot_worlds.artifacts import public_error
from robot_worlds.codex_client import CodexClient
from robot_worlds.editing import materialize
from robot_worlds.llm import audit_value
from robot_worlds.policy import LLMAgent
from robot_worlds.sdk import Report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', required=True, type=Path)
    parser.add_argument('--root-object', required=True)
    parser.add_argument('--offset', required=True, type=float, nargs=3)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--codex-executable', default='codex')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    parent = load(args.world)
    before = materialize(parent.spec)
    members = {args.root_object}
    assert args.root_object in {o.id for o in before.objects}, 'Unknown assembly root'
    while True:
        expanded = members | {o.id for o in before.objects if o.parent_id in members}
        if expanded == members:
            break
        members = expanded
    scope = sorted(members | {r.id for r in before.regions if r.interior_of in members})
    clients = {
        'edit': CodexClient(model='gpt-6-astra', reasoning='medium', executable=args.codex_executable,
                            max_calls=2, max_seconds=360, timeout=180, max_total_tokens=120000),
        'policy': CodexClient(model='gpt-6-astra', reasoning='medium', executable=args.codex_executable,
                              max_calls=12, max_seconds=420, timeout=180, max_total_tokens=450000),
    }
    record = {'workflow_completed': False, 'task_success': False, 'parent_world_id': parent.id,
              'root_object': args.root_object, 'offset_m': args.offset, 'scope': scope,
              'transport': 'codex', 'model': 'gpt-6-astra', 'events': []}

    def save():
        record.update(elapsed_seconds=time.monotonic()-started,
                      model_usage={phase: audit_value(client.usage) for phase, client in clients.items()})
        (args.output/'acceptance.json').write_text(json.dumps(record, indent=2)+'\n')

    def event(value):
        if value.get('type') in {'edit_started', 'world_created', 'validation', 'step', 'episode_finished'}:
            record['events'].append({'type': value['type'], 'elapsed_seconds': time.monotonic()-started})
            print(value['type'], flush=True)
            save()

    try:
        prompt = (f'Translate the complete assembly rooted at {args.root_object} by world XYZ '
                  f'{args.offset} metres. Move all its descendants and owned regions together. '
                  'Preserve every other object, the task, rubric, robot start, source geometry and all '
                  'physical parameters. This widens clearance beside the passive conveyor; do not '
                  'add equipment, relocate the conveyor endpoints or change the navigation task.')
        world = parent.refine(prompt, scope=scope, client=clients['edit'], max_repairs=1,
                              output=args.output/'worlds', on_event=event)
        after = materialize(world.spec)
        before_data, after_data = before.model_dump(mode='json'), after.model_dump(mode='json')
        for section in ('objects', 'regions'):
            old = {o['id']: o for o in before_data[section]}
            new = {o['id']: o for o in after_data[section]}
            assert old.keys() == new.keys(), 'Edit changed entity identities'
            for oid, original in old.items():
                expected = dict(original)
                if oid in scope:
                    expected['position'] = [a+b for a, b in zip(original['position'], args.offset)]
                assert new[oid] == expected, f'Unexpected edit to {oid}'
        for field in ('task', 'rubric', 'robot', 'robot_start', 'bounds', 'process_cycles'):
            assert before_data[field] == after_data[field], f'Changed protected field: {field}'
        assert world.manifest['validation']['status'] == 'ready'
        record.update(world_id=world.id, validation=world.manifest['validation'],
                      exact_scope_and_protections=True, edited_entities=len(scope))
        save()
        report = world.evaluate(LLMAgent(client=clients['policy'], max_steps=12, max_seconds=420),
                                max_steps=12, max_sim_seconds=90, output=args.output/'runs',
                                max_replay_frames=12, on_event=event)
        record.update(report_id=report.path.name, summary=report.summary,
                      task_success=report.summary['successes'] > 0,
                      termination=report.result['episodes'][0]['termination'])
        for artifact, directory in ((world, 'restored-world'), (report, 'restored-report')):
            archive = artifact.export(args.output/(directory+'.zip'))
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(args.output/directory)
        restored = load(args.output/'restored-world')
        restored_report = Report(args.output/'restored-report')
        assert restored.id == world.id
        assert restored_report.summary == report.summary
        assert restored_report.regrade(restored, output=args.output/'regraded').summary == report.summary
        record.update(workflow_completed=report.summary['valid'] == 1, portable_reload_and_regrade=True)
    except Exception as error:
        record['error'] = public_error(error)
    finally:
        save()
        print(json.dumps({key: record.get(key) for key in
                          ('workflow_completed', 'task_success', 'elapsed_seconds', 'error')}), flush=True)
    return 0 if record['workflow_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
